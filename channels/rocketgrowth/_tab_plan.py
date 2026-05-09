"""탭 1: 발주 계획 (자매 페이지 lines 401-1374 의 신규 계획 모드 이전).

C-1 (완료): 파일 업로드 + 자동 분류 + 파싱 + 마스터 로드
C-2 (완료): 발주 계획 산출 + 팔레트 최적화 + 편집 UI
C-3 (현재): 저장 (InboundPlan + PlanFile) + 쿠팡 입고생성 양식.xlsx 다운로드
"""
from __future__ import annotations

import io
from datetime import date as _date
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import select

from rocketgrowth.config import load_config
from rocketgrowth.db import get_session
from rocketgrowth.file_classifier import (
    FILE_TYPE_COUPANG, FILE_TYPE_WMS, FILE_TYPE_TEMPLATE, FILE_TYPE_MOVEMENT,
    FILE_TYPE_LABELS, classify_uploaded_files,
)
from rocketgrowth.export import (
    ExportItem, dates_from_batch, default_expiry_dates,
    extract_template_option_ids, fill_coupang_template,
)
from rocketgrowth.ingestion.coupang_file import parse_coupang_inventory_file
from rocketgrowth.ingestion.wms_file import aggregate_wms_by_barcode, parse_wms_inventory_file
from rocketgrowth.models import CoupangProduct, InboundPlan, InboundPlanItem, PlanFile, WmsProduct
from rocketgrowth.planning import PlanInput, PlanParams, compute_plan, urgency_badge
from rocketgrowth.pallet import PalletItem, optimize_to_pallet
from rocketgrowth.outbound import PoolAllocationItem, allocate_parent_pool
from sqlalchemy import desc

from channels.rocketgrowth._helpers import (
    derive_substatus_label, ni, load_plan_files, resolve_parent_barcode,
    save_plan, section_note,
)


_BRAND_TO_COMPANY = {
    'nenu':    '서현',
    'cachers': '캐처스',
}


class _DBFile:
    """PlanFile DB 에서 로드한 raw 파일을 file_uploader 출력 (UploadedFile) 형태로 mock."""
    def __init__(self, name: str, content: bytes):
        self.name = name
        self._content = content

    def getvalue(self) -> bytes:
        return self._content

    def read(self) -> bytes:
        return self._content

    def seek(self, *_args, **_kwargs):
        return 0


_UPLOAD_GUIDE_ROWS = [
    ("WMS 재고 파일", FILE_TYPE_WMS,
     "다원WMS > 재고관리 > 창고별로케이션별재고(OWNER) > [품목-정상,불량-로케이션-로트] 탭 > 검색 > 우클릭, Export(Excel)",
     "Document_YYYY-MM-DD.xls"),
    ("쿠팡 재고 파일", FILE_TYPE_COUPANG,
     "쿠팡Wing > 로켓그로스 > 재고현황 > 엑셀 다운로드",
     "inventory_health_sku_info_YYYYMMDDhhmmss.xlsx"),
    ("쿠팡 입고생성 파일", FILE_TYPE_TEMPLATE,
     "쿠팡Wing > 로켓그로스 > 입고관리 > 새로운 입고 생성 > 엑셀 다운로드",
     "generated_excel.xlsx"),
    ("재고이동 파일", FILE_TYPE_MOVEMENT,
     "이번달 '쿠팡 재고이동건' 파일",
     "쿠팡 재고이동건_YYYY_MM월.xlsx"),
]


def _render_upload_guide(brand_company: str, group=None) -> str:
    """단일 업체 기준 업로드 가이드 HTML."""
    body = ""
    for label, ft, path, fname_example in _UPLOAD_GUIDE_ROWS:
        mark = "✅" if group and ft in group.files else ""
        mark_cell = f'<td style="width:60px; text-align:center;">{mark}</td>'
        body += (
            f"<tr><td>{label}</td>"
            f'<td><code style="font-size:0.85em;">{fname_example}</code></td>'
            f"<td>{path}</td>{mark_cell}</tr>"
        )
    return (
        '<table style="border-collapse: collapse; width: 100%;">'
        '<thead><tr>'
        '<th style="text-align:left;">구분</th>'
        '<th style="text-align:left;">파일명 예시</th>'
        '<th style="text-align:left;">취합 경로</th>'
        '<th style="width:60px; text-align:center;">취합</th>'
        '</tr></thead>'
        f'<tbody>{body}</tbody>'
        '</table>'
    )


@st.cache_data(show_spinner="쿠팡 재고 파싱 중...")
def _parse_cp_cached(data: bytes, name: str):
    tmp = Path("./_tmp_cp_" + name)
    tmp.write_bytes(data)
    try:
        return parse_coupang_inventory_file(tmp)
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


@st.cache_data(show_spinner="WMS 재고 파싱 중...")
def _parse_wms_cached(data: bytes, name: str):
    tmp = Path("./_tmp_wms_" + name)
    tmp.write_bytes(data)
    try:
        return parse_wms_inventory_file(tmp)
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


def _extract_template_option_ids(template_file) -> set[int]:
    tmp = Path("./_tmp_tpl_" + template_file.name)
    tmp.write_bytes(template_file.getvalue())
    try:
        return extract_template_option_ids(tmp)
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


def _render_plan_picker(brand: str, brand_company: str) -> int | None:
    """발주 계획 dropdown — '+ 신규' + 기존 plan 목록.

    반환:
      None  → 신규 계획 (현재 동작 유지)
      int   → 선택된 plan_id (조회 모드)
    """
    with get_session() as s:
        plans = s.execute(
            select(InboundPlan)
            .where(InboundPlan.company_name == brand_company)
            .order_by(desc(InboundPlan.id)).limit(30)
        ).scalars().all()

    # 각 plan 의 attach_pdf 보유 여부 (검수 진행중 / 임시저장 구분용)
    plan_ids = [p.id for p in plans]
    has_attach: set[int] = set()
    if plan_ids:
        with get_session() as s:
            attach_rows = s.execute(
                select(PlanFile.plan_id).where(
                    PlanFile.plan_id.in_(plan_ids),
                    PlanFile.file_type == "attach_pdf",
                )
            ).scalars().all()
        has_attach = set(attach_rows)

    NEW = "__new__"
    options: list = [NEW] + plan_ids

    def _label(opt):
        if opt == NEW:
            return "+ 신규 계획"
        p = next((p for p in plans if p.id == opt), None)
        if not p:
            return f"#{opt}"
        sub = derive_substatus_label(p, has_attach_pdf=(p.id in has_attach))
        date_str = str(p.plan_date or "")
        fc_str = f" · FC {p.fc_name}" if p.fc_name else ""
        return f"#{p.id} {sub} · {date_str}{fc_str}"

    sel = st.selectbox(
        "발주 계획 선택",
        options=options,
        format_func=_label,
        index=0,  # default = 신규 계획
        key=f"rg_{brand}_tab1_plan_picker",
    )
    return None if sel == NEW else int(sel)


def _render_saved_plan_view(brand: str, brand_company: str, plan_id: int):
    """저장된 plan 의 발주계획 내용 조회 — 메트릭 + SKU + 파일 + 다음 단계."""
    import math as _math
    cfg = load_config()
    with get_session() as s:
        plan = s.get(InboundPlan, plan_id)
        if plan is None:
            st.error(f"plan #{plan_id} 을 찾지 못했습니다.")
            return
        items = s.execute(
            select(InboundPlanItem).where(InboundPlanItem.plan_id == plan_id)
            .order_by(InboundPlanItem.coupang_option_id)
        ).scalars().all()

    plan_files = load_plan_files(plan_id)
    sub_label = derive_substatus_label(plan, has_attach_pdf=("attach_pdf" in plan_files))

    # 컨텍스트 바
    fc = plan.fc_name or "미정"
    arr = plan.arrival_date or "미정"
    worker = plan.worker or "미정"
    milkrun = plan.milkrun_id or "미정"
    parts = [
        f'<span style="background:#fef3c7; color:#92400e; padding:3px 8px; '
        f'border-radius:4px; font-weight:700;">#{plan.id}</span>',
        f'<span>{sub_label}</span>',
        f'<span><b>업체</b> {plan.company_name}</span>',
        f'<span><b>FC</b> {fc}</span>',
        f'<span><b>입고일</b> {arr}</span>',
        f'<span><b>작업자</b> {worker}</span>',
        f'<span><b>milkrun_id</b> {milkrun}</span>',
    ]
    st.markdown(
        '<div style="display:flex; flex-wrap:wrap; gap:12px; align-items:center; '
        'padding:8px 12px; background:#f9fafb; border:1px solid #e5e7eb; '
        'border-radius:6px; margin:0 0 10px 0; font-size:0.92em;">'
        + "".join(parts) + "</div>",
        unsafe_allow_html=True,
    )

    if not items:
        st.warning("이 plan 에 저장된 SKU 가 없습니다.")
        return

    # 메트릭
    total_qty = sum(int(i.inbound_qty_final or 0) for i in items)
    active = [i for i in items if (i.inbound_qty_final or 0) > 0]
    total_boxes = sum(
        _math.ceil((i.inbound_qty_final or 0) / max(int(i.box_qty or 1), 1))
        for i in active
    )
    psz = cfg.pallet_size_boxes
    pallet_decimal = (total_boxes / psz) if psz else 0.0
    pallet_full = (total_boxes // psz) if psz else 0
    pallet_remainder = total_boxes - pallet_full * psz if psz else total_boxes
    if pallet_remainder == 0 and pallet_full > 0:
        pallet_disp = f"{pallet_full} (꽉참)"
    else:
        pallet_disp = f"{pallet_decimal:.2f}({pallet_full}+{pallet_remainder}박스)"
    weight_kg = float(plan.total_weight_kg) if plan.total_weight_kg else 0.0

    c1, c2, c3, c4, c5 = st.columns([1, 1, 1.5, 1, 1])
    c1.metric("확정 수량 (낱개)", f"{total_qty:,}")
    c2.metric("확정 박스수", f"{total_boxes:,}")
    c3.metric("팔레트", pallet_disp)
    c4.metric("총중량 (kg)", f"{weight_kg:,.1f}")
    c5.metric("대상 SKU", f"{len(active)}")

    # SKU 목록
    with st.expander(f"📋 SKU 목록 ({len(items)}건)", expanded=False):
        df_items = pd.DataFrame([{
            "옵션ID": i.coupang_option_id,
            "상품명": i.product_name,
            "확정(낱개)": i.inbound_qty_final,
            "확정(box)": _math.ceil((i.inbound_qty_final or 0) / max(int(i.box_qty or 1), 1)),
            "박스인입": i.box_qty,
            "7일판매": i.sales_7d,
            "30일판매": i.sales_30d,
            "팔레트번호": i.pallet_no,
        } for i in items])
        st.dataframe(df_items, width="stretch", hide_index=True, height=320)

    # 업로드된 파일 목록
    if plan_files:
        with st.expander(f"📎 업로드된 파일 ({len(plan_files)}개)", expanded=False):
            for ftype, (fname, fbytes) in plan_files.items():
                st.download_button(
                    f"{ftype} — {fname} ({len(fbytes) / 1024:,.1f} KB)",
                    data=fbytes,
                    file_name=fname,
                    key=f"rg_{brand}_savedview_dl_{plan_id}_{ftype}",
                )

    # 다음 단계 버튼 (탭 2 결과물 패키지로 이동)
    st.divider()
    import streamlit.components.v1 as components
    if st.button(
        "다음 단계 →",
        key=f"rg_{brand}_savedview_next_{plan_id}",
        type="primary",
        width="stretch",
        help="결과물 패키지 탭으로 이동.",
    ):
        # 탭 2 의 plan picker 가 이 plan_id 를 자동 선택하도록 session 저장
        st.session_state[f'rg_{brand}_last_saved_plan_id'] = plan_id
        components.html(
            """
            <script>
            const tabs = window.parent.document.querySelectorAll('button[role="tab"]');
            if (tabs.length > 1) {
                tabs[1].click();
                window.parent.scrollTo({top: 0, behavior: 'smooth'});
            }
            </script>
            """,
            height=0,
        )


def render(brand: str):
    """탭 1 메인 진입점.

    brand: 'nenu' (=서현) | 'cachers'
    """
    cfg = load_config()
    brand_company = _BRAND_TO_COMPANY[brand]

    # ─── 0. 발주 계획 선택 (신규 / 기존) ──────────────────
    selected_plan_id = _render_plan_picker(brand, brand_company)

    # picker 전환 시 stale state 정리 (기존 plan → 신규, 또는 plan A → plan B)
    _prev_key = f"rg_{brand}_prev_picker"
    _prev = st.session_state.get(_prev_key)
    if _prev != selected_plan_id:
        # last_saved 는 picker 가 set 한 값으로 곧 덮어씀 — 신규 모드 진입 시는 clear
        if selected_plan_id is None:
            st.session_state.pop(f"rg_{brand}_last_saved_plan_id", None)
        # plan 전환 시 이전 편집 세션 / loaded 플래그 모두 정리
        for _k in list(st.session_state.keys()):
            if isinstance(_k, str) and (
                _k.startswith(f"rg_{brand}_inbound_final::")
                or _k.startswith(f"rg_{brand}_loaded_for_plan_")
            ):
                st.session_state.pop(_k, None)
    st.session_state[_prev_key] = selected_plan_id

    plan_params = PlanParams(
        lead_time_days=cfg.lead_time_days,
        target_cover_days=cfg.target_cover_days,
        velocity_alpha=cfg.velocity_alpha,
        overstock_days=cfg.overstock_days,
    )
    st.info(
        f"📐 **계산 파라미터**: 리드타임 **{plan_params.lead_time_days}일** · "
        f"목표 커버 **{plan_params.target_cover_days}일** · "
        f"판매속도 블렌딩 α={plan_params.velocity_alpha:.2f} (7일 평균 가중) · "
        f"과잉 경고 {plan_params.overstock_days}일 초과",
        icon="ℹ️",
    )

    # 기존 plan 선택 모드 → DB에서 raw 파일 로드 + 검토 모드 진입
    selected_plan_obj = None
    if selected_plan_id is not None:
        with get_session() as _s:
            selected_plan_obj = _s.get(InboundPlan, selected_plan_id)
        if selected_plan_obj is None:
            st.error(f"plan #{selected_plan_id} 을 찾지 못했습니다.")
            return
        # verified/completed 는 read-only summary
        if selected_plan_obj.status in ("verified", "completed"):
            _render_saved_plan_view(brand, brand_company, selected_plan_id)
            return
        # qty_confirmed/draft 는 편집 가능
        plan_files_db = load_plan_files(selected_plan_id)
        missing = [
            t for t in ("coupang_raw", "wms_raw", "template")
            if t not in plan_files_db
        ]
        if missing:
            st.error(
                f"plan #{selected_plan_id} 의 raw 파일 누락: {missing}. "
                "신규 계획으로 다시 진행해 주세요."
            )
            return
        coupang_file = _DBFile(*plan_files_db["coupang_raw"])
        wms_file = _DBFile(*plan_files_db["wms_raw"])
        template_file = _DBFile(*plan_files_db["template"])
        movement_file = _DBFile(*plan_files_db["movement"]) if "movement" in plan_files_db else None
        # 수량확정 = 재확정 모드 (existing_plan_id 로 update)
        st.session_state[f"rg_{brand}_last_saved_plan_id"] = selected_plan_id
    else:
        # 신규 계획 — file uploader UI
        st.markdown("##### 1-1 기초자료 업로드")
        section_note("입고수량확정에 필요한 기초 자료를 취합하여 업로드해 주세요.")

        _guide_ph = st.empty()
        _guide_ph.markdown(_render_upload_guide(brand_company, None), unsafe_allow_html=True)

        uploaded_files = st.file_uploader(
            f"파일 업로드 — {brand_company}",
            type=["xlsx", "xls"],
            accept_multiple_files=True,
            key=f"rg_{brand}_upload",
            label_visibility="collapsed",
        )

        if not uploaded_files:
            st.info(f"파일을 업로드하세요. {brand_company} 의 파일 4종을 한 번에 올릴 수 있습니다.")
            return

        # 자동 분류
        classified, company_groups = classify_uploaded_files(uploaded_files)

        # 화주 강제 — 사용자 선택한 채널의 화주만 사용
        group = company_groups.get(brand_company)
        _guide_ph.markdown(
            _render_upload_guide(brand_company, group), unsafe_allow_html=True
        )

        if group is None:
            st.error(
                f"**{brand_company}** 업체로 식별된 파일이 없습니다. "
                "상품 정보 관리에 해당 업체 상품이 등록되어 있는지 확인하세요."
            )
            # 미분류 파일 경고
            unclassified = [cf for cf in classified if not cf.company]
            if unclassified:
                st.warning(
                    f"⚠️ {len(unclassified)}개 파일의 업체를 식별 못 했습니다: "
                    + ", ".join(cf.file.name for cf in unclassified)
                )
            return

        # 업로드된 파일 중 다른 업체 분류 발견 시 안내 (이 채널에서는 무시)
        other_brands = [c for c in company_groups if c != brand_company]
        if other_brands:
            st.warning(
                f"이 채널은 **{brand_company}** 전용입니다. 다른 업체로 분류된 파일은 무시됩니다: "
                f"{', '.join(other_brands)}"
            )

        # 필수 파일 체크 — 4종 모두 필수 (캐처스/네뉴 동일)
        coupang_file = group.files.get(FILE_TYPE_COUPANG)
        wms_file = group.files.get(FILE_TYPE_WMS)
        template_file = group.files.get(FILE_TYPE_TEMPLATE)
        movement_file = group.files.get(FILE_TYPE_MOVEMENT)

        if group.missing_types:
            labels = [FILE_TYPE_LABELS[ft] for ft in group.missing_types]
            st.info(f"**{brand_company}** 미감지 파일: {', '.join(labels)}")

        if not (coupang_file and wms_file and template_file and movement_file):
            st.warning(f"**{brand_company}** 의 필수 파일 4종이 모두 필요합니다.")
            return

    # 파싱
    try:
        cp_snap = _parse_cp_cached(coupang_file.getvalue(), coupang_file.name)
        wms_snap = _parse_wms_cached(wms_file.getvalue(), wms_file.name)
        wms_agg = aggregate_wms_by_barcode(wms_snap)
        tpl_option_ids = _extract_template_option_ids(template_file)
    except Exception as ex:
        st.error(f"파일 파싱 실패: {ex}")
        return

    cp_total_before = len(cp_snap.rows)
    if tpl_option_ids:
        cp_snap.rows = [
            r for r in cp_snap.rows if r.coupang_option_id in tpl_option_ids
        ]

    st.success(
        f"✅ {brand_company} 파일 업로드 완료 — "
        f"쿠팡 재고 {len(cp_snap.rows)}/{cp_total_before} 행 (입고생성 양식 옵션 ID 필터), "
        f"WMS {len(wms_snap.rows)} 행, "
        f"WMS 바코드 합산 {len(wms_agg)} 종"
    )

    # 마스터 로드
    with get_session() as session:
        cp_masters = session.execute(
            select(CoupangProduct).where(CoupangProduct.company_name == brand_company)
        ).scalars().all()
        wms_masters = session.execute(
            select(WmsProduct).where(WmsProduct.company_name == brand_company)
        ).scalars().all()

    st.caption(
        f"📚 **{brand_company}** 마스터: 쿠팡 상품 {len(cp_masters)}개 · "
        f"WMS 상품 {len(wms_masters)}개"
    )

    cp_master_by_opt = {m.coupang_option_id: m for m in cp_masters}
    wms_master_by_bc = {m.wms_barcode: m for m in wms_masters}
    wms_master_by_opt = {m.coupang_option_id: m for m in wms_masters if m.coupang_option_id}

    include_all = False  # 비관리 SKU 항상 제외

    # ─── 4. 기본 추천 수량 계산 (판매 기반) ──────────────────────
    rows = []
    for cp in cp_snap.rows:
        cm = cp_master_by_opt.get(cp.coupang_option_id)
        if not cm:
            if not include_all:
                continue
        else:
            if not cm.milkrun_managed and not include_all:
                continue

        parent_bc, unit_qty = resolve_parent_barcode(
            cm, wms_master_by_bc, wms_master_by_opt
        ) if cm else (None, 1)
        own_bc = cm.wms_barcode if cm else None
        own_wp = wms_master_by_bc.get(own_bc) if own_bc else None
        parent_wp = wms_master_by_bc.get(parent_bc) if parent_bc else None
        box_qty = (
            (own_wp.box_qty if own_wp and own_wp.box_qty else None)
            or (parent_wp.box_qty if parent_wp and parent_wp.box_qty else None)
            or 1
        )
        shelf_life = (
            (own_wp.shelf_life_days if own_wp else None)
            or (parent_wp.shelf_life_days if parent_wp else None)
        )
        weight_g = (
            (own_wp.weight_g if own_wp and own_wp.weight_g else None)
            or (parent_wp.weight_g if parent_wp and parent_wp.weight_g else None)
            or 0
        )

        engine_out = compute_plan(
            PlanInput(
                coupang_option_id=cp.coupang_option_id,
                product_name=cp.product_name,
                option_name=cp.option_name,
                orderable_stock=cp.orderable_stock,
                inbound_stock=cp.inbound_stock,
                sales_qty_7d=cp.sales_qty_7d,
                sales_qty_30d=cp.sales_qty_30d,
                box_qty=box_qty,
            ),
            plan_params,
        )

        wms_product_name = (
            (own_wp.product_name if own_wp and own_wp.product_name else None)
            or (parent_wp.product_name if parent_wp and parent_wp.product_name else None)
            or cp.product_name
            or (cm.product_name if cm else "")
        )

        rows.append({
            "urgency": urgency_badge(engine_out.urgency),
            "urgency_key": engine_out.urgency,
            "coupang_option_id": cp.coupang_option_id,
            "parent_wms_barcode": parent_bc,
            "own_wms_barcode": own_bc,
            "unit_qty": unit_qty,
            "product_name": wms_product_name,
            "orderable": cp.orderable_stock,
            "inbound_stock": cp.inbound_stock,
            "sales_7d": cp.sales_qty_7d,
            "sales_30d": cp.sales_qty_30d,
            "velocity": round(engine_out.sales_velocity_daily, 2),
            "days_until_stockout": engine_out.days_until_stockout,
            "stock_at_arrival": round(engine_out.stock_at_arrival, 1),
            "target_at_arrival": round(engine_out.target_at_arrival, 1),
            "stock_2w": round(engine_out.stock_after_2w, 1),
            "stock_4w": round(engine_out.stock_after_4w, 1),
            "box_qty": box_qty,
            "basic_boxes": engine_out.inbound_boxes,
            "inbound_basic": engine_out.inbound_qty_suggested,
            "inbound_pallet": engine_out.inbound_qty_suggested,
            "pallet_boxes": engine_out.inbound_boxes,
            "pallet_adjusted": False,
            "inbound_final": engine_out.inbound_qty_suggested,
            "days_sellable_after": (
                round(engine_out.days_sellable_after, 1)
                if engine_out.days_sellable_after else None
            ),
            "shelf_life_days": shelf_life,
            "weight_g": weight_g,
            "master_missing": cm is None,
        })

    if not rows:
        st.warning(
            "표시할 SKU가 없습니다. 상품 정보 관리에서 milkrun_managed=True 로 설정된 "
            f"{brand_company} 옵션이 있는지 확인하세요."
        )
        return

    base_df = pd.DataFrame(rows)

    # ─── 4-2. 팔레트 최적화 (토글은 아래 7-2 에서 렌더, 여기는 값만 읽음) ───
    pallet_on_key = f"rg_{brand}_pallet_on"
    pallet_on = st.session_state.get(pallet_on_key, True)

    # 팔레트 토글 변경 시 편집 잔재 cleanup
    _prev_key = f"rg_{brand}_pallet_on_prev"
    _prev = st.session_state.get(_prev_key)
    if _prev is not None and _prev != pallet_on:
        for _k in list(st.session_state.keys()):
            if isinstance(_k, str) and _k.startswith(f"rg_{brand}_inbound_final::"):
                st.session_state.pop(_k, None)
    st.session_state[_prev_key] = pallet_on

    if pallet_on:
        initial_pools: dict[str, int] = {}
        for bc, agg in wms_agg.items():
            total_avail = sum(b.get("available") or 0 for b in (agg.get("batches") or []))
            initial_pools[bc] = int(total_avail)
        for _, row in base_df.iterrows():
            pbc = row["parent_wms_barcode"]
            if not pbc:
                continue
            basic_units = int(row["basic_boxes"]) * int(row["box_qty"]) * int(row["unit_qty"])
            initial_pools[pbc] = max(0, initial_pools.get(pbc, 0) - basic_units)

        pallet_items = [
            PalletItem(
                key=int(row["coupang_option_id"]),
                urgency=row["urgency_key"],
                basic_boxes=int(row["basic_boxes"] or 0),
                box_qty=int(row["box_qty"] or 1),
                unit_qty=int(row["unit_qty"] or 1),
                parent_barcode=row["parent_wms_barcode"],
                current_total_stock=int((row["orderable"] or 0) + (row["inbound_stock"] or 0)),
                velocity=float(row["velocity"] or 0),
                days_until_stockout=row["days_until_stockout"],
            )
            for _, row in base_df.iterrows()
        ]
        pallet_result = optimize_to_pallet(
            pallet_items,
            initial_pools,
            pallet_size=cfg.pallet_size_boxes,
            overstock_days=None,
            rounding="up",
            cap_per_sku=None,
        )
        for i, row in base_df.iterrows():
            key = int(row["coupang_option_id"])
            opt_boxes = int(pallet_result.optimized_boxes.get(key, row["basic_boxes"] or 0))
            opt_qty = opt_boxes * int(row["box_qty"] or 1)
            base_df.at[i, "pallet_boxes"] = opt_boxes
            base_df.at[i, "inbound_pallet"] = opt_qty
            base_df.at[i, "pallet_adjusted"] = opt_boxes != int(row["basic_boxes"] or 0)
            base_df.at[i, "inbound_final"] = opt_qty
    else:
        pallet_result = None

    # ─── 5. 편집 세션 (스냅샷 단위 격리) ────────────────────────
    _session_key = (
        f"rg_{brand}_inbound_final::{cp_snap.snapshot_date}::{wms_snap.snapshot_date}"
    )
    if _session_key not in st.session_state:
        st.session_state[_session_key] = {}

    # 기존 plan 편집 모드 — 저장된 inbound_qty_final 로 세션 초기화 (1회)
    if (selected_plan_obj is not None
        and not st.session_state.get(f"rg_{brand}_loaded_for_plan_{selected_plan_obj.id}")):
        with get_session() as _s:
            _saved_items = _s.execute(
                select(InboundPlanItem)
                .where(InboundPlanItem.plan_id == selected_plan_obj.id)
            ).scalars().all()
        for it in _saved_items:
            st.session_state[_session_key][int(it.coupang_option_id)] = int(
                it.inbound_qty_final or 0
            )
        st.session_state[f"rg_{brand}_loaded_for_plan_{selected_plan_obj.id}"] = True

    for i, row in base_df.iterrows():
        opt = int(row["coupang_option_id"])
        if opt in st.session_state[_session_key]:
            base_df.at[i, "inbound_final"] = st.session_state[_session_key][opt]

    # ─── 6. 부모 풀 할당 ────────────────────────────────────────
    def _allocate(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["selected_batch_expiry"] = None
        df["selected_status"] = None
        df["pool_total_base"] = None
        df["pool_remaining_base"] = None
        df["max_single_batch_after"] = None
        _wms_agg_norm = {str(k).strip().upper(): v for k, v in wms_agg.items()}

        for parent_bc, group in df.groupby("parent_wms_barcode", sort=False, dropna=False):
            if not parent_bc:
                for idx in group.index:
                    df.at[idx, "selected_status"] = "no_parent"
                continue
            agg = wms_agg.get(parent_bc) or _wms_agg_norm.get(str(parent_bc).strip().upper())
            batches = (agg or {}).get("batches") or []
            total_base = sum(b.get("available") or 0 for b in batches)

            items = [
                PoolAllocationItem(
                    key=int(row["coupang_option_id"]),
                    unit_qty=int(row["unit_qty"] or 1),
                    requested_qty=int(row["inbound_final"] or 0),
                )
                for _, row in group.iterrows()
            ]
            results, _ = allocate_parent_pool(items, batches)
            result_by_key = {r.key: r for r in results}
            for idx, row in group.iterrows():
                r = result_by_key[int(row["coupang_option_id"])]
                df.at[idx, "selected_batch_expiry"] = r.selected_batch_expiry
                df.at[idx, "selected_status"] = r.status
                df.at[idx, "pool_total_base"] = total_base
                df.at[idx, "pool_remaining_base"] = r.pool_remaining_base_after
                df.at[idx, "max_single_batch_after"] = r.max_single_batch_after
        return df

    allocated_df = _allocate(base_df)

    # ─── 6a. WMS 매칭 진단 ─────────────────────────────────────
    _wms_keys_norm = {str(k).strip().upper() for k in wms_agg.keys()}
    _missing_parents = []
    for _bc in base_df["parent_wms_barcode"].dropna().unique():
        if str(_bc).strip().upper() not in _wms_keys_norm:
            _rows = base_df[base_df["parent_wms_barcode"] == _bc]
            _names = _rows["product_name"].dropna().unique().tolist()
            _missing_parents.append({
                "parent_wms_barcode": _bc,
                "상품명": ", ".join(_names[:2]),
                "SKU수": len(_rows),
            })
    if _missing_parents:
        with st.expander(
            f"⚠️ WMS 파일에서 못 찾은 parent 바코드 ({len(_missing_parents)}건) — 현재고 0",
            expanded=False,
        ):
            st.caption(
                "원인: (1) WMS 파일에 해당 바코드 재고 없음 / "
                "(2) 상품 정보 관리의 parent_wms_barcode 가 실제 WMS 와 다름 / "
                "(3) 모든 재고가 RELEASEAREA(출고대기) LOC."
            )
            st.dataframe(pd.DataFrame(_missing_parents), width="stretch", hide_index=True)

    def _calc_confirmed_boxes(r):
        v = r["inbound_final"]
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        import math as _math
        box = max(int(r["box_qty"] or 1), 1)
        qty = int(v)
        # 박스 미충족도 1박스로 계산 (ceil). 예: box=50, qty=48 -> 1
        return _math.ceil(qty / box)

    allocated_df["confirmed_boxes"] = allocated_df.apply(_calc_confirmed_boxes, axis=1)

    # ─── 7. 재발주 알림 ────────────────────────────────────────
    st.markdown("##### 1-2 입고 수량 확정")
    section_note(
        "재발주 필요 품목 Check 항목 및 입고 수량을 검토한 후, "
        "'입고 수량 확정' 버튼을 눌러주세요. (저장은 C-3 단계에서 활성화)"
    )

    reproduction_lead = cfg.reproduction_lead_days
    pool_velocity: dict[str, float] = {}
    for _, r in allocated_df.iterrows():
        p = r["parent_wms_barcode"]
        if not p:
            continue
        pool_velocity[p] = pool_velocity.get(p, 0.0) + float(r["velocity"] or 0) * int(r["unit_qty"] or 1)

    pool_stats = (
        allocated_df[allocated_df["parent_wms_barcode"].notna()]
        .groupby("parent_wms_barcode", sort=False)
        .agg(
            item_count=("coupang_option_id", "count"),
            allocated_base=(
                "inbound_final",
                lambda s: int((s.fillna(0) * allocated_df.loc[s.index, "unit_qty"]).sum()),
            ),
            pool_total=("pool_total_base", "first"),
            pool_remaining=("pool_remaining_base", "min"),
            first_product=("product_name", "first"),
        )
        .reset_index()
    )
    pool_stats["pool_velocity"] = pool_stats["parent_wms_barcode"].map(pool_velocity).fillna(0)
    pool_stats["reproduction_demand"] = pool_stats["pool_velocity"] * reproduction_lead
    pool_stats["shortfall"] = pool_stats["reproduction_demand"] - pool_stats["pool_remaining"]
    pool_stats["needs_reproduction"] = (
        (pool_stats["shortfall"] > 0)
        | (pool_stats["allocated_base"] > pool_stats["pool_total"])
    )

    single_product_per_pool = (
        allocated_df[
            (allocated_df["parent_wms_barcode"].notna())
            & (allocated_df["unit_qty"].fillna(1).astype(int) == 1)
        ]
        .groupby("parent_wms_barcode", sort=False)["product_name"]
        .first()
    )
    pool_stats["single_product"] = (
        pool_stats["parent_wms_barcode"].map(single_product_per_pool).fillna(pool_stats["first_product"])
    )

    repro_list = pool_stats[pool_stats["needs_reproduction"]].sort_values("shortfall", ascending=False)

    with st.expander(
        "🏭 재발주 필요 품목 Check"
        + (f" · 재생산 리드타임 {reproduction_lead}일 기준" if len(repro_list) > 0 else ""),
        expanded=len(repro_list) > 0,
    ):
        if len(repro_list) == 0:
            st.caption("✅ 단품 재고가 재생산 리드타임 동안 자력 운영 가능")
        else:
            display = repro_list[[
                "parent_wms_barcode", "single_product", "pool_total", "allocated_base",
                "pool_remaining", "pool_velocity", "reproduction_demand", "shortfall",
            ]].copy()
            for _c in ["pool_velocity", "reproduction_demand", "shortfall"]:
                display[_c] = pd.to_numeric(display[_c], errors="coerce").fillna(0)
            display["pool_velocity"] = display["pool_velocity"].round(0).astype(int)
            display["reproduction_demand"] = display["reproduction_demand"].round(0).astype(int)
            display["shortfall"] = (-display["shortfall"]).round(0).astype(int)
            display = display.rename(columns={
                "parent_wms_barcode": "WMS바코드",
                "single_product": "상품명",
                "pool_total": "현재고",
                "allocated_base": "이번출고",
                "pool_remaining": "출고후잔여",
                "pool_velocity": "일소요",
                "reproduction_demand": f"{reproduction_lead}일소요",
                "shortfall": f"{reproduction_lead}일후부족",
            })
            st.dataframe(display, width="stretch", hide_index=True)
            st.warning(
                f"⚠️ {len(repro_list)}개 품목이 재생산 리드타임({reproduction_lead}일) 동안 버티지 못함. "
                "생산/발주 담당자에게 재발주 검토 요청."
            )

    # ─── 7-2. 팔레트 토글 ──────────────────────────────────────
    st.checkbox(
        f"🚛 팔레트 단위 최적화 (1팔레트 = {cfg.pallet_size_boxes}박스)",
        value=pallet_on,
        key=pallet_on_key,
        help=(
            f"체크 시: 총 박스수가 {cfg.pallet_size_boxes}의 배수가 되도록 올림 → 팔레트 꽉 채움. "
            "체크 해제 시: 엔진 기본 추천 그대로."
        ),
    )

    # ─── 8. 편집 테이블 ────────────────────────────────────────
    allocated_df["pool_remaining_bundle"] = allocated_df.apply(
        lambda r: (
            int(int(r["pool_remaining_base"]) // max(int(r["unit_qty"] or 1), 1))
            if r["pool_remaining_base"] is not None
               and not (isinstance(r["pool_remaining_base"], float) and pd.isna(r["pool_remaining_base"]))
            else None
        ),
        axis=1,
    )

    col_f1, col_f2 = st.columns([2, 1])
    with col_f1:
        search = st.text_input(
            "🔍 상품명 / 옵션ID 검색",
            key=f"rg_{brand}_search",
            help="여러 개 쉼표/공백 구분. 예: '비타민, 94917143993'",
        )
    with col_f2:
        status_options = ["🚨 긴급", "⚠️ 보충", "✅ 안정", "❄️ 과잉", "⏸ 무판매"]
        status_filter = st.multiselect(
            "상태 필터",
            options=status_options,
            default=["🚨 긴급", "⚠️ 보충"],
            key=f"rg_{brand}_status_filter",
        )

    view = allocated_df.copy()
    if search:
        import re as _re
        terms = [t.strip() for t in _re.split(r"[,\s]+", search) if t.strip()]
        if terms:
            name_series = view["product_name"].fillna("").astype(str)
            opt_series = view["coupang_option_id"].fillna("").astype(str)
            mask = pd.Series(False, index=view.index)
            for t in terms:
                tl = t.lower()
                mask = mask | name_series.str.lower().str.contains(tl, regex=False) \
                            | opt_series.str.contains(t, regex=False)
            view = view[mask]
    if status_filter:
        view = view[view["urgency"].isin(status_filter)]

    display_cols = [
        "coupang_option_id", "urgency", "product_name",
        "orderable", "sales_7d", "sales_30d", "velocity", "days_until_stockout",
        "box_qty", "inbound_basic", "basic_boxes",
        "pool_remaining_base", "pool_remaining_bundle",
        "inbound_final", "confirmed_boxes",
        "selected_batch_expiry", "selected_status",
    ]

    DEFAULT_CONFIRM_BG = "#fff8d6"
    OVER_CONFIRM_BG = "background-color: #ff6b6b; color: white; font-weight: bold;"
    OVER_STOCK_BG = "background-color: #ffe5e5;"
    PALLET_ADJUSTED_BG = "background-color: #cceeff; font-weight: bold;"

    def _highlight_over(row):
        styles = [""] * len(row)
        pool_rem = row.get("pool_remaining_base")
        status = row.get("selected_status")
        is_over = (
            (pool_rem is not None
             and not (isinstance(pool_rem, float) and pd.isna(pool_rem))
             and pool_rem < 0)
            or status == "insufficient"
        )
        cols = list(row.index)
        if is_over:
            if "inbound_final" in cols:
                styles[cols.index("inbound_final")] = OVER_CONFIRM_BG
            for col in ("pool_remaining_base", "pool_remaining_bundle"):
                if col in cols:
                    styles[cols.index(col)] = OVER_STOCK_BG
        else:
            try:
                inbound_final = row.get("inbound_final")
                basic_boxes = row.get("basic_boxes")
                box_qty = row.get("box_qty")
                if (inbound_final is not None
                    and not (isinstance(inbound_final, float) and pd.isna(inbound_final))
                    and basic_boxes is not None
                    and not (isinstance(basic_boxes, float) and pd.isna(basic_boxes))
                    and box_qty
                    and int(inbound_final) != int(basic_boxes) * int(box_qty)):
                    if "inbound_final" in cols:
                        styles[cols.index("inbound_final")] = PALLET_ADJUSTED_BG
                    if "confirmed_boxes" in cols:
                        styles[cols.index("confirmed_boxes")] = PALLET_ADJUSTED_BG
            except (ValueError, TypeError):
                pass
        return styles

    view_styled = (
        view[display_cols]
        .style.set_properties(subset=["inbound_final"], **{"background-color": DEFAULT_CONFIRM_BG})
        .apply(_highlight_over, axis=1)
    )

    editor_key = f"rg_{brand}_editor_{cp_snap.snapshot_date}_{wms_snap.snapshot_date}"
    edited = st.data_editor(
        view_styled,
        key=editor_key,
        width="stretch",
        height=500,
        hide_index=True,
        disabled=[c for c in display_cols if c != "inbound_final"],
        column_config={
            "urgency": st.column_config.TextColumn(
                "상태", width="small", pinned=True,
                help="🚨 긴급 · ⚠️ 보충 · ✅ 안정 · ❄️ 과잉 · ⏸ 무판매",
            ),
            "coupang_option_id": st.column_config.NumberColumn(
                "옵션ID", format="%d", width="small", pinned=True,
                help="쿠팡 옵션 ID",
            ),
            "product_name": st.column_config.TextColumn("상품명", width="large", pinned=True),
            "orderable": st.column_config.NumberColumn("쿠팡가용", format="%d"),
            "sales_7d": st.column_config.NumberColumn("7일", format="%d"),
            "sales_30d": st.column_config.NumberColumn("30일", format="%d"),
            "velocity": st.column_config.NumberColumn(
                "속도/일", format="%.2f",
                help=f"판매 속도 = α×(7일/7) + (1-α)×(30일/30), α={plan_params.velocity_alpha}",
            ),
            "days_until_stockout": st.column_config.NumberColumn(
                "소진예상(일)", format="%.1f",
            ),
            "box_qty": st.column_config.NumberColumn("box입인", format="%d"),
            "inbound_basic": st.column_config.NumberColumn(
                "입고권장(낱개)", format="%d",
                help="엔진 기본 추천 — 팔레트 꽉 채움 적용 전",
            ),
            "basic_boxes": st.column_config.NumberColumn(
                "입고권장(box)", format="%d",
            ),
            "inbound_final": st.column_config.NumberColumn(
                "확정", format="%d", required=False,
                help="사용자가 직접 입력. 권장입고수 참고하여 결정",
            ),
            "confirmed_boxes": st.column_config.NumberColumn(
                "확정(box)", format="%d",
                help="박스인입수 미충족도 1박스로 계산 (ceil). 예: box=50, qty=48 → 1",
            ),
            "selected_batch_expiry": st.column_config.DateColumn("소비기한"),
            "selected_status": None,
            "pool_remaining_base": st.column_config.NumberColumn("재고(낱개)", format="%d"),
            "pool_remaining_bundle": st.column_config.NumberColumn("재고(번들)", format="%d"),
        },
    )

    # 편집본 → session_state 반영
    changed = False
    for _, erow in edited.iterrows():
        opt = int(erow["coupang_option_id"])
        raw_val = erow.get("inbound_final")
        if raw_val is None or (isinstance(raw_val, float) and pd.isna(raw_val)):
            if opt in st.session_state[_session_key]:
                del st.session_state[_session_key][opt]
                changed = True
        else:
            new_val = ni(raw_val) or 0
            if st.session_state[_session_key].get(opt) != new_val:
                st.session_state[_session_key][opt] = new_val
                changed = True
    if changed:
        st.rerun()

    # 경고 배너
    insufficient = allocated_df[allocated_df["selected_status"] == "insufficient"]
    no_parent = allocated_df[allocated_df["selected_status"] == "no_parent"]
    if len(insufficient) > 0:
        st.warning(
            f"⚠️ {len(insufficient)}개 SKU: 단일 배치로 확정수량 커버 불가."
        )
    if len(no_parent) > 0:
        st.info(f"ℹ️ {len(no_parent)}개 SKU: 부모 WMS 바코드 매핑 없음.")

    # ─── 9. 요약 메트릭 ────────────────────────────────────────
    _edited_qty_by_opt = {
        int(r["coupang_option_id"]): (
            None if pd.isna(r.get("inbound_final")) else int(r["inbound_final"])
        )
        for _, r in edited.iterrows()
    }
    confirmed_qty = 0
    confirmed_boxes_sum = 0
    active_cnt = 0
    total_weight_g = 0
    for _, r in allocated_df.iterrows():
        opt_id = int(r["coupang_option_id"])
        if opt_id in _edited_qty_by_opt:
            qty = _edited_qty_by_opt[opt_id] or 0
        else:
            raw = r.get("inbound_final")
            qty = int(raw) if raw is not None and not (isinstance(raw, float) and pd.isna(raw)) else 0
        if qty > 0:
            active_cnt += 1
            import math as _math
            box = int(r.get("box_qty") or 1)
            # 박스수: ceil — 박스인입 미충족도 1박스 (확정(box) 컬럼과 동일)
            box_val = _math.ceil(qty / max(box, 1))
            confirmed_qty += qty
            confirmed_boxes_sum += box_val
            unit_w = int(r.get("weight_g") or 0)
            total_weight_g += unit_w * qty + 500 * box_val

    total_weight_kg = total_weight_g / 1000
    _pallet_sz = cfg.pallet_size_boxes

    def _fmt_boxes(v: float) -> str:
        # 정수면 정수, 아니면 소수점 1자리
        return f"{int(v):,}" if float(v).is_integer() else f"{v:,.1f}"

    # 팔레트 컬럼 폭을 1.5배로 확장 (소수점 표현 텍스트 잘림 방지)
    col_s1, col_s2, col_s3, col_s4, col_s5 = st.columns([1, 1, 1.5, 1, 1])
    col_s1.metric("확정 수량 (낱개)", f"{confirmed_qty:,}")
    col_s2.metric("확정 박스수", _fmt_boxes(confirmed_boxes_sum))
    if _pallet_sz:
        pallet_decimal = confirmed_boxes_sum / _pallet_sz
        pallet_full = int(confirmed_boxes_sum // _pallet_sz)
        pallet_remainder = round(confirmed_boxes_sum - pallet_full * _pallet_sz, 1)
    else:
        pallet_decimal = 0.0
        pallet_full = 0
        pallet_remainder = confirmed_boxes_sum
    if pallet_remainder == 0 and pallet_full > 0:
        pallet_disp = f"{pallet_full} (꽉참)"
    else:
        pallet_disp = f"{pallet_decimal:.2f}({pallet_full}+{_fmt_boxes(pallet_remainder)}박스)"
    col_s3.metric("팔레트", pallet_disp)
    col_s4.metric(
        "총중량 (kg)",
        f"{total_weight_kg:,.1f}",
        help="(WMS 단위중량 × 확정수량 + 500g × 박스수) ÷ 1000",
    )
    col_s5.metric("대상 SKU", f"{active_cnt}")

    # 팔레트 최적화 상세
    _pallets_already_full = (
        confirmed_boxes_sum > 0
        and float(confirmed_boxes_sum % _pallet_sz) == 0.0
    )
    if pallet_on and pallet_result is not None and pallet_result.mode != "noop" and not _pallets_already_full:
        with st.expander(
            f"🎯 팔레트 최적화 ({pallet_result.mode}, "
            f"{pallet_result.applied_delta:+d}박스, "
            f"{pallet_result.total_boxes_before}→{pallet_result.total_boxes_after})",
            expanded=False,
        ):
            if pallet_result.unfilled > 0:
                st.warning(
                    f"⚠️ 제약(부모 풀 여유)으로 {pallet_result.unfilled} 박스 추가 충진 불가."
                )
            if pallet_result.adjustments:
                adj_map: dict[int, int] = {}
                for k, d in pallet_result.adjustments:
                    adj_map[k] = adj_map.get(k, 0) + d
                adj_df = pd.DataFrame([{"옵션ID": k, "박스 조정": v} for k, v in adj_map.items()])
                adj_df = adj_df.merge(
                    allocated_df[["coupang_option_id", "product_name"]],
                    left_on="옵션ID", right_on="coupang_option_id", how="left",
                )[["옵션ID", "product_name", "박스 조정"]]
                adj_df.columns = ["옵션ID", "상품명", "박스 조정"]
                st.dataframe(adj_df, width="stretch", hide_index=True)
            else:
                st.caption("조정 없음")

    # session 에 결과 저장 — C-3 의 저장 단계 + 탭 2/3 재사용
    st.session_state[f'rg_{brand}_cp_snap'] = cp_snap
    st.session_state[f'rg_{brand}_wms_snap'] = wms_snap
    st.session_state[f'rg_{brand}_wms_agg'] = wms_agg
    st.session_state[f'rg_{brand}_movement_file'] = movement_file
    st.session_state[f'rg_{brand}_allocated_df'] = allocated_df
    st.session_state[f'rg_{brand}_edited_df'] = edited
    st.session_state[f'rg_{brand}_total_weight_kg'] = total_weight_kg
    st.session_state[f'rg_{brand}_files_for_save'] = {
        'coupang_file': coupang_file,
        'wms_file': wms_file,
        'template_file': template_file,
        'movement_file': movement_file,
    }

    st.divider()

    # ─── 입고 수량 확정 + 저장 + 쿠팡 양식 다운로드 (C-3) ─────────
    saved_state_key = f"rg_{brand}_last_saved_plan_id"
    last_saved = st.session_state.get(saved_state_key)

    # 재고 부족 체크 — 확정수량 > 0 인데 단일 배치로 커버 불가한 SKU
    insufficient_to_save = allocated_df[
        (allocated_df["selected_status"] == "insufficient")
        & (allocated_df["inbound_final"].fillna(0).astype(int) > 0)
    ]

    # ─── 수량확정 버튼 (idempotent: 재클릭 시 기존 plan update) ─────
    if confirmed_qty == 0:
        st.button(
            "수량확정",
            disabled=True, width="stretch",
            help="확정 수량 1개 이상 입력 필요.",
            key=f"rg_{brand}_qty_btn_no_qty",
        )
        st.caption("확정 수량을 입력한 후 이 버튼 → DB 저장 + 쿠팡 입고생성 양식 다운로드.")
        return

    if len(insufficient_to_save) > 0:
        st.error(
            f"⚠️ **재고 부족 SKU {len(insufficient_to_save)}건** 으로 수량확정 불가. "
            "해당 SKU 의 확정 수량을 0 또는 가용 재고 이내로 조정 후 다시 시도하세요."
        )
        with st.expander(f"부족 SKU 목록 ({len(insufficient_to_save)}건)", expanded=True):
            _disp = insufficient_to_save[[
                "coupang_option_id", "product_name", "inbound_final",
                "pool_remaining_base", "max_single_batch_after",
            ]].copy()
            _disp.columns = ["옵션ID", "상품명", "확정수량", "출고후잔여(낱개)", "단일배치 최대"]
            st.dataframe(_disp, width="stretch", hide_index=True)
        st.button(
            "수량확정",
            disabled=True, width="stretch",
            help="재고 부족 SKU 해결 후 활성화.",
            key=f"rg_{brand}_qty_btn_blocked",
        )
        return

    # 활성 수량확정 버튼 (재클릭 시 update)
    btn_label = (
        f"수량확정 재확정 ({confirmed_qty:,}개)" if last_saved
        else f"수량확정 ({confirmed_qty:,}개)"
    )
    btn_help = (
        "기존 plan 의 items 갱신 + 쿠팡 입고생성 양식 재생성." if last_saved
        else "DB 저장 (status=수량확정) + 쿠팡 입고생성 양식 자동 생성. 탭 2 결과물 패키지로 이어서 진행."
    )
    if st.button(
        btn_label,
        type="primary", width="stretch",
        help=btn_help,
        key=f"rg_{brand}_qty_btn",
    ):
        try:
            # 편집본을 allocated_df 에 머지
            save_df = allocated_df.copy()
            for _, erow in edited.iterrows():
                opt = int(erow["coupang_option_id"])
                mask = save_df["coupang_option_id"] == opt
                save_df.loc[mask, "inbound_final"] = ni(erow["inbound_final"]) or 0

            _raw_files: dict[str, tuple[str, bytes]] = {}
            if coupang_file:
                _raw_files["coupang_raw"] = (coupang_file.name, coupang_file.getvalue())
            if wms_file:
                _raw_files["wms_raw"] = (wms_file.name, wms_file.getvalue())
            if template_file:
                _raw_files["template"] = (template_file.name, template_file.getvalue())

            shipment_type = cfg.default_shipment_type
            plan_id = save_plan(
                cp_snap=cp_snap, wms_snap=wms_snap, full_df=save_df,
                company_name=brand_company,
                shipment_type=shipment_type,
                total_weight_kg=total_weight_kg,
                movement_blob=movement_file.getvalue() if movement_file else None,
                movement_filename=movement_file.name if movement_file else None,
                raw_files=_raw_files,
                existing_plan_id=last_saved,  # 있으면 update, 없으면 신규
            )
            st.session_state[saved_state_key] = plan_id
            verb = "재확정" if last_saved else "수량확정"
            st.success(f"✅ {verb} 완료 (plan_id={plan_id}) — 쿠팡 양식 다운로드 가능")
            st.rerun()
        except Exception as ex:
            st.error(f"저장 실패: {ex}")

    # 수량확정 전이면 download/next 미노출
    if not last_saved:
        return

    # ─── 수량확정 후: 쿠팡 양식 + 다음 단계 ─────────────────────
    st.divider()
    st.success(
        f"✅ 수량확정 완료 (plan_id={last_saved}). "
        "아래 쿠팡 입고생성 양식 다운로드 후 쿠팡 Wing 에 업로드. "
        "탭 2 (결과물 패키지) 에서 검수 + 물류센터 전달 패키지 진행."
    )

    if not template_file:
        st.error("쿠팡 입고생성 양식 파일이 없습니다. 위에서 재업로드 필요.")
        return

    # 저장된 plan 의 items 에서 ExportItem 생성
    # save_df 사용 (이미 allocated_df + 편집본 머지 상태)
    save_df = allocated_df.copy()
    for _, erow in edited.iterrows():
        opt = int(erow["coupang_option_id"])
        mask = save_df["coupang_option_id"] == opt
        save_df.loc[mask, "inbound_final"] = ni(erow["inbound_final"]) or 0

    export_items: list[ExportItem] = []
    for _, row in save_df.iterrows():
        qty = int(row["inbound_final"] or 0)
        if qty <= 0:
            continue
        own_bc = row.get("own_wms_barcode")
        shelf = row.get("shelf_life_days")
        wms_short = row.get("selected_batch_expiry")
        if wms_short:
            exp_d, man_d = dates_from_batch(wms_short, shelf)
        else:
            exp_d, man_d = default_expiry_dates(shelf)
        export_items.append(ExportItem(
            coupang_option_id=int(row["coupang_option_id"]),
            inbound_qty=qty,
            shelf_life_days=int(shelf) if shelf else None,
            expiry_date=exp_d,
            manufacture_date=man_d,
            wms_barcode=own_bc,
            product_name=row.get("product_name"),
        ))

    if not export_items:
        st.warning("입고 수량 > 0 인 SKU 가 없어 양식을 생성하지 않습니다.")
        return

    try:
        xlsx_bytes, missing = fill_coupang_template(
            io.BytesIO(template_file.getvalue()),
            export_items,
            delete_non_target=True,
        )
    except Exception as ex:
        st.error(f"쿠팡 양식 생성 실패: {ex}")
        return

    out_name = f"generated_excel_{_date.today().isoformat()}.xlsx"
    st.download_button(
        f"📥 쿠팡 입고생성 양식 다운로드 ({out_name})",
        data=xlsx_bytes,
        file_name=out_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", width="stretch",
        key=f"rg_{brand}_dl_coupang",
    )

    if missing:
        st.warning(
            f"⚠️ {len(missing)}건 누락 (쿠팡 양식에 없는 옵션 ID). "
            "쿠팡 입고생성 파일을 새로 받아 업로드 필요."
        )
        with st.expander("누락 옵션 목록", expanded=False):
            st.dataframe(pd.DataFrame(missing), width="stretch", hide_index=True)

    # 다음 단계 (결과물 패키지 탭으로 이동) — 스크롤 없이 탭 전환
    import streamlit.components.v1 as components
    if st.button(
        "다음 단계 →",
        key=f"rg_{brand}_goto_pack",
        type="primary",
        width="stretch",
        help="결과물 패키지 탭으로 자동 이동 + 페이지 상단으로 스크롤.",
    ):
        components.html(
            """
            <script>
            const tabs = window.parent.document.querySelectorAll('button[role="tab"]');
            if (tabs.length > 1) {
                tabs[1].click();
                window.parent.scrollTo({top: 0, behavior: 'smooth'});
            }
            </script>
            """,
            height=0,
        )

    # 새 작업 시작 버튼
    if st.button(
        "🔄 새 작업 시작 (이 plan 저장 상태 클리어)",
        key=f"rg_{brand}_clear_saved",
        help="저장된 plan 은 그대로 DB에 남고, 화면만 신규 작업 모드로 초기화.",
    ):
        st.session_state.pop(saved_state_key, None)
        # 편집 세션도 cleanup
        for _k in list(st.session_state.keys()):
            if isinstance(_k, str) and _k.startswith(f"rg_{brand}_inbound_final::"):
                st.session_state.pop(_k, None)
        st.rerun()
