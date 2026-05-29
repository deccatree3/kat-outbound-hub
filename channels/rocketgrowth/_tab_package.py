"""탭 2: 결과물 패키지 (자매 페이지 lines 1375-1990 의 기존 계획 관리 모드 일부 이전).

흐름:
  1. plan 로드 (방금 탭 1 에서 저장한 plan 또는 dropdown 으로 선택)
  2. 회차 컨텍스트 표시
  3. 메타 입력 (FC, 작업자, 입고예정일, 밀크런ID)
  4. 쿠팡 입고생성 양식 재생성/다운로드
  5. 쿠팡 결과물 PDF 3종 업로드 (부착/동봉/바코드)
  6. 검수 (verify)
  7. SKU별 검수 결과
  8. 발주 확정 버튼 → status=verified
  9. 물류센터 전달 4종 파일 다운로드 (취합리스트/팔레트적재/재고이동/PDF리네임)

운송방식 분기 (밀크런/택배) 와 화주 분기 (네뉴/캐처스) 는 다음 단계.
"""
from __future__ import annotations

import io
from datetime import date as _date, timedelta
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy import desc, select

from rocketgrowth.config import load_config
from rocketgrowth.coupang_result import (
    parse_attachment_doc, parse_barcode_labels, parse_invoice_doc,
    parse_parcel_attachment_doc,
)
from rocketgrowth.db import get_session
from rocketgrowth.models import (
    CoupangProduct, CoupangResultLog, InboundPlan, InboundPlanItem, PlanFile, WmsProduct,
)
from rocketgrowth.outbound import PoolAllocationItem, allocate_parent_pool
from rocketgrowth.pallet_assign import (
    PalletAssignment, PalletEntry, PalletItem as PA_PalletItem, assign_pallets as pa_assign_pallets,
)
from rocketgrowth.secondary_export import SecondaryItem
from rocketgrowth.verification import (
    PlannedSku, derive_attached_barcode, is_label_expected, verify,
)

from channels.rocketgrowth._helpers import (
    AGETSHOT_BOX_CAPACITY, STATUS_LABELS, derive_substatus_label, format_plan_label,
    get_fc_info, is_agetshot_bundle, jump_to_tab, load_plan_files, resolve_parent_barcode,
    save_plan_files, section_note, upsert_fc_info,
)


_BRAND_TO_COMPANY = {
    'nenu':    '서현',
    'cachers': '캐처스',
}


def _render_context_bar(plan: InboundPlan, has_attach_pdf: bool = False) -> str:
    """회차 컨텍스트 바 — plan 메타 한 줄 표시."""
    sid = f"#{plan.id}"
    status_label = derive_substatus_label(plan, has_attach_pdf=has_attach_pdf)
    company = plan.company_name or "—"
    fc = plan.fc_name or "미정"
    arr = plan.arrival_date or "미정"  # 첨부문서 파싱 전엔 미정
    milkrun = plan.milkrun_id or "미정"
    parts = [
        f'<span style="background:#fef3c7; color:#92400e; padding:3px 8px; '
        f'border-radius:4px; font-weight:700;">{sid}</span>',
        f'<span>{status_label}</span>',
        f'<span><b>업체</b> {company}</span>',
        f'<span><b>FC</b> {fc}</span>',
        f'<span><b>입고일</b> {arr}</span>',
        f'<span><b>milkrun_id</b> {milkrun}</span>',
    ]
    return (
        '<div style="display:flex; flex-wrap:wrap; gap:12px; align-items:center; '
        'padding:8px 12px; background:#f9fafb; border:1px solid #e5e7eb; '
        'border-radius:6px; margin:0 0 10px 0; font-size:0.92em;">'
        + "".join(parts) + "</div>"
    )


def _select_plan(brand: str, brand_company: str) -> InboundPlan | None:
    """업체별 plan dropdown. 기본값은 sentinel(미선택). 단, 다른 탭의
    '다음 단계 →' 버튼이 set 한 pending_pick_plan_id 가 있으면 자동 선택."""
    with get_session() as s:
        plans = s.execute(
            select(InboundPlan)
            .where(InboundPlan.company_name == brand_company)
            .order_by(desc(InboundPlan.id))  # # 번호 큰 것 (최근) 이 상단
        ).scalars().all()

    if not plans:
        st.info(f"📭 **{brand_company}** 의 저장된 plan 이 없습니다. 탭 1 에서 먼저 발주 계획 저장 필요.")
        return None

    # attach_pdf 보유 여부 (검수 진행중 vs 임시저장 구분)
    plan_ids = [p.id for p in plans]
    with get_session() as s:
        attach_rows = s.execute(
            select(PlanFile.plan_id).where(
                PlanFile.plan_id.in_(plan_ids),
                PlanFile.file_type == "attach_pdf",
            )
        ).scalars().all()
    has_attach = set(attach_rows)

    SENTINEL = -1
    labels = {SENTINEL: "— 발주계획 선택 —"}
    for i, p in enumerate(plans):
        labels[i] = format_plan_label(p, has_attach_pdf=(p.id in has_attach))

    # 다른 탭의 '다음 단계 →' 가 set 한 pending plan 이 있으면 selectbox 에 1회 적용
    sel_key = f"pkg_{brand_company}_plan_select"
    active_key = f"pkg_{brand_company}_active_plan_id"  # 안전망: 선택된 plan_id 영속
    pending = st.session_state.pop(f"rg_{brand}_pending_pkg_pick", None)
    if pending is not None:
        target = next((i for i, p in enumerate(plans) if p.id == pending), None)
        if target is not None:
            st.session_state[sel_key] = target
            st.session_state[active_key] = pending
    elif active_key in st.session_state:
        # active_key 가 set 되어 있으면 항상 sync (Streamlit widget state lost
        # 케이스 대비 — sel_key 가 미존재/sentinel/유효값 무관 강제 동기화)
        prev_id = st.session_state[active_key]
        target = next((i for i, p in enumerate(plans) if p.id == prev_id), None)
        if target is not None:
            cur = st.session_state.get(sel_key)
            # 사용자가 명시적으로 다른 plan 으로 바꿨으면 (cur != target, cur != SENTINEL) 존중
            if cur is None or cur == SENTINEL or cur == target:
                st.session_state[sel_key] = target

    sel = st.selectbox(
        "발주 계획 선택",
        options=[SENTINEL] + list(range(len(plans))),
        format_func=lambda o: labels[o],
        index=0,
        key=sel_key,
    )
    if sel == SENTINEL:
        # active_key 는 유지 — Streamlit 위젯 상태가 우연히 sentinel 로 빠져도
        # 다음 render 에서 안전망(sel_key not in state 일 때)이 복원하도록.
        # 사용자가 진짜 다른 plan 선택 시 active_key 는 selected.id 로 갱신.
        return None
    selected = plans[sel]
    st.session_state[active_key] = selected.id
    return selected


SHIPMENT_LABELS = {'milkrun': '밀크런', 'parcel': '택배'}


def _derive_meta(plan: InboundPlan) -> dict[str, Any]:
    """plan 레코드에서 메타 자동 derive — 입력 UI 제거 후 사용.

    fc_name / milkrun_id / arrival_date 는 검수 단계에서 첨부문서 파싱 결과로
    추후 보정될 수 있음 (verify_section 내부에서 attachment.fc / attachment.milkrun_id /
    attachment.arrival_date 사용).

    worker 는 plan 의 company_name 기준 (= 화주) — 설정의 default_company_name 사용 X.
    """
    return {
        'fc_name': plan.fc_name or "동탄1",
        'worker': plan.worker or plan.company_name or "서현",
        'arrival_date': plan.arrival_date or plan.plan_date or _date.today(),
        'milkrun_id': plan.milkrun_id,
        'shipment_type': plan.shipment_type or 'milkrun',
    }


def render(brand: str):
    """탭 2 메인."""
    cfg = load_config()
    brand_company = _BRAND_TO_COMPANY[brand]

    plan = _select_plan(brand, brand_company)
    if plan is None:
        return

    plan_files = load_plan_files(plan.id)
    st.markdown(
        _render_context_bar(plan, has_attach_pdf=("attach_pdf" in plan_files)),
        unsafe_allow_html=True,
    )

    # ─── 입고방법 선택 (밀크런 / 택배) ─────────────────────
    # 입고확정 이상은 변경 불가 (이미 운송수단 결정됨)
    _ship_locked = (plan.status or "") in ("inbound_confirmed", "verified", "completed")
    _ship_options = ['milkrun', 'parcel']
    _ship_labels = {'milkrun': '밀크런', 'parcel': '택배'}
    _cur_ship = plan.shipment_type if plan.shipment_type in _ship_options else 'milkrun'
    selected_ship = st.radio(
        "입고방법",
        options=_ship_options,
        format_func=lambda v: _ship_labels.get(v, v),
        index=_ship_options.index(_cur_ship),
        horizontal=True,
        disabled=_ship_locked,
        key=f"pkg_{brand}_ship_select_{plan.id}",
        help=(
            "입고확정 이후엔 변경 불가." if _ship_locked
            else "밀크런: 팔레트 단위 트럭. 택배: 박스 단위."
        ),
    )
    # 변경 시 DB 저장
    if not _ship_locked and selected_ship != plan.shipment_type:
        try:
            with get_session() as _ss:
                _p = _ss.get(InboundPlan, plan.id)
                _p.shipment_type = selected_ship
                _ss.commit()
            plan.shipment_type = selected_ship
            st.rerun()
        except Exception as ex:
            st.error(f"입고방법 저장 실패: {ex}")

    # ─── 공통 데이터 로드 ──────────────────────────────────
    with get_session() as ms:
        items = ms.execute(
            select(InboundPlanItem).where(
                InboundPlanItem.plan_id == plan.id,
                InboundPlanItem.inbound_qty_final > 0,
            )
        ).scalars().all()
        cp_masters_list = ms.execute(
            select(CoupangProduct).where(CoupangProduct.company_name == brand_company)
        ).scalars().all()
        wms_masters_list = ms.execute(
            select(WmsProduct).where(WmsProduct.company_name == brand_company)
        ).scalars().all()

    cp_master_by_opt = {m.coupang_option_id: m for m in cp_masters_list}
    wms_master_by_bc = {m.wms_barcode: m for m in wms_masters_list}
    wms_master_by_opt = {m.coupang_option_id: m for m in wms_masters_list if m.coupang_option_id}

    if not items:
        st.warning("이 계획에 확정 수량(>0) SKU가 없습니다. 탭 1 로 돌아가서 확정 수량 입력 후 저장.")
        return

    is_completed = plan.status == "completed"

    # ─── 메타 자동 derive (입력 UI 제거됨) ───────────────────
    # FC / 송장ID / 입고예정일 은 검수 단계에서 첨부문서 파싱 결과로 보정됨.
    meta = _derive_meta(plan)

    # ─── ① 쿠팡 입고생성 계획 ───────────────────
    import math as _math
    st.subheader("① 쿠팡 입고생성 계획")
    section_note("아래 계획대로 Wing에서 입고생성을 해주세요.")

    # 에이지샷 번들 식별 (캐처스 전용 — box인입 = FREE, capacity=100)
    def _is_agetshot_for(i):
        if brand != 'cachers':
            return False
        cm_i = cp_master_by_opt.get(i.coupang_option_id)
        own_bc = cm_i.wms_barcode if cm_i else None
        wms_i = wms_master_by_bc.get(own_bc) if own_bc else None
        if wms_i is None and cm_i and cm_i.coupang_option_id in wms_master_by_opt:
            wms_i = wms_master_by_opt[cm_i.coupang_option_id]
        return is_agetshot_bundle(cm_i, wms_i)

    def _boxes_for_item(i):
        qty = int(i.inbound_qty_final or 0)
        if qty <= 0:
            return 0
        if _is_agetshot_for(i):
            return _math.ceil(qty / AGETSHOT_BOX_CAPACITY)
        return _math.ceil(qty / max(int(i.box_qty or 1), 1))

    # 메트릭 — 박스수/팔레트 ceil 기반 (탭 1 과 동일, 에이지샷 분기 포함)
    total_qty = int(sum(int(i.inbound_qty_final or 0) for i in items))
    total_boxes = int(sum(_boxes_for_item(i) for i in items))
    psz = cfg.pallet_size_boxes
    if psz:
        pallet_decimal = total_boxes / psz
        pallet_full = total_boxes // psz
        pallet_remainder = total_boxes - pallet_full * psz
    else:
        pallet_decimal = 0.0; pallet_full = 0; pallet_remainder = total_boxes
    if pallet_remainder == 0 and pallet_full > 0:
        pallet_disp = f"{pallet_full} (꽉참)"
    else:
        pallet_disp = f"{pallet_decimal:.2f}({pallet_full}+{pallet_remainder}박스)"
    weight_kg = float(plan.total_weight_kg) if plan.total_weight_kg else 0.0

    mc1, mc2, mc3, mc4, mc5 = st.columns([1, 1, 1, 1.5, 1])
    mc1.metric("SKU", f"{len(items)}")
    mc2.metric("확정수량", f"{total_qty:,}")
    mc3.metric("박스수", f"{total_boxes:,}")
    mc4.metric("팔레트", pallet_disp)
    mc5.metric("총중량 (kg)", f"{weight_kg:,.1f}")

    # 계획 상세 — 항상 표시 (접기 X)
    def _box_compositions(qty: int, box_qty: int) -> list[tuple[int, int]]:
        """확정 수량 + 박스인입 -> 쿠팡 Wing '박스 구성' [(per_box, num_boxes), ...].

        - qty == 0:                      [(0, 0)]
        - qty <= box_qty:                [(qty, 1)]              (단일 부분 박스)
        - qty % box_qty == 0:            [(box_qty, qty/box_qty)] (전 박스 동일)
        - 그 외 (잔여 있고 다중 박스): [(box_qty, full), (rem, 1)]
            예: 98/50 -> [(50, 1), (48, 1)] = 50개 1박스 + 48개 1박스
            예: 75/50 -> [(50, 1), (25, 1)] = 50개 1박스 + 25개 1박스
            예: 48/18 -> [(18, 2), (12, 1)] = 18개 2박스 + 12개 1박스
        """
        if not qty:
            return [(0, 0)]
        bq = max(int(box_qty or 1), 1)
        q = int(qty)
        if q <= bq:
            return [(q, 1)]
        full = q // bq
        rem = q % bq
        if rem == 0:
            return [(bq, full)]
        return [(bq, full), (rem, 1)]

    # SKU 마다 박스 구성에 따라 1+ 행 생성 (에이지샷 번들은 box인입=FREE 단일 행)
    plan_rows = []
    for i in items:
        cm = cp_master_by_opt.get(i.coupang_option_id)
        name = (
            f"{(cm.product_name if cm else (i.product_name or ''))} "
            f"{(cm.option_name if cm else (i.option_name or ''))}"
        ).strip()
        qty = int(i.inbound_qty_final or 0)
        expiry = i.wms_short_expiry
        if _is_agetshot_for(i):
            # 에이지샷 번들: 100 단위로 행 분할. 각 행 박스수=1.
            # 예: 105 -> [100, 5] = 2행, 박스수=1 each
            if qty <= 0:
                plan_rows.append({
                    "상품명": name, "상품수": qty,
                    "box인입": "0", "박스수": 0,
                    "소비기한": expiry,
                })
            else:
                full = qty // AGETSHOT_BOX_CAPACITY
                rem = qty % AGETSHOT_BOX_CAPACITY
                for _ in range(full):
                    plan_rows.append({
                        "상품명": name, "상품수": qty,
                        "box인입": str(AGETSHOT_BOX_CAPACITY),  # "100"
                        "박스수": 1,
                        "소비기한": expiry,
                    })
                if rem > 0:
                    plan_rows.append({
                        "상품명": name, "상품수": qty,
                        "box인입": str(rem),
                        "박스수": 1,
                        "소비기한": expiry,
                    })
        else:
            for per_box, num_boxes in _box_compositions(qty, i.box_qty):
                plan_rows.append({
                    "상품명": name,
                    "상품수": qty,
                    "box인입": str(per_box),
                    "박스수": num_boxes,
                    "소비기한": expiry,
                })
    plan_df = pd.DataFrame(plan_rows)

    st.dataframe(
        plan_df, width="stretch", hide_index=True, height=380,
        column_config={
            "상품명": st.column_config.TextColumn("상품명", width="large"),
            "상품수": st.column_config.NumberColumn(
                "상품수", format="%d",
                help="해당 SKU 의 확정 수량 (행 분할되어도 동일 — 같은 SKU 임을 표시)",
            ),
            "box인입": st.column_config.TextColumn(
                "box인입",
                help="박스 1개당 상품 수. 에이지샷 번들 (캐처스): 'FREE' (인박스/아웃박스 룰).",
            ),
            "박스수": st.column_config.NumberColumn("박스수", format="%d"),
            "소비기한": st.column_config.DateColumn("소비기한", format="YYYY-MM-DD"),
        },
    )

    # ─── SecondaryItem + PalletAssignment 빌드 ─────────────
    sec_items: list[SecondaryItem] = []
    for it in items:
        cm = cp_master_by_opt.get(it.coupang_option_id)
        own = cm.wms_barcode if cm else None
        wp = wms_master_by_bc.get(own) if own else None
        pbc, uq = resolve_parent_barcode(cm, wms_master_by_bc, wms_master_by_opt) if cm else (None, 1)
        pwp = wms_master_by_bc.get(pbc) if pbc else None
        wg = (wp.weight_g if wp and wp.weight_g else 0) or (pwp.weight_g if pwp and pwp.weight_g else 0)
        shl = (wp.shelf_life_days if wp else None) or (pwp.shelf_life_days if pwp else None)
        mfg = None
        exp_d = it.wms_short_expiry
        if exp_d and shl:
            mfg = exp_d - timedelta(days=int(shl) - 1)
        cpn = cm.product_name if cm else (it.product_name or "")
        cpo = cm.option_name if cm else it.option_name
        wmsn = (wp.product_name if wp and wp.product_name else None) or (pwp.product_name if pwp and pwp.product_name else None)
        bq = it.box_qty or 1
        boxes = (it.inbound_qty_final or 0) // max(bq, 1)
        sec_items.append(SecondaryItem(
            coupang_option_id=it.coupang_option_id,
            sku_id=cm.sku_id if cm else None,
            coupang_product_id=cm.coupang_product_id if cm else None,
            product_name=cpn, option_name=cpo, wms_product_name=wmsn,
            own_wms_barcode=own,
            coupang_barcode=cm.coupang_barcode if cm else None,
            parent_wms_barcode=pbc, unit_qty=uq,
            inbound_qty=it.inbound_qty_final or 0,
            box_qty=bq, boxes=boxes,
            weight_g=int(wg or 0), expiry_date=exp_d,
            manufacture_date=mfg, shelf_life_days=int(shl) if shl else None,
        ))

    # PalletAssignment — 저장된 pallet_no 가 있으면 그대로, 없으면 재할당
    has_pallet_no = any(it.pallet_no for it in items)
    if has_pallet_no:
        pallet_map: dict[int, list[PalletEntry]] = {}
        for it in items:
            pn = it.pallet_no or 1
            boxes_it = (it.inbound_qty_final or 0) // max(it.box_qty or 1, 1)
            if boxes_it <= 0:
                continue
            pallet_map.setdefault(pn, []).append(
                PalletEntry(key=it.coupang_option_id, name=it.product_name or "", boxes=boxes_it)
            )
        # pallet_count: plan.total_pallets 우선 (저장 시 정확한 값) — 없으면 max(pallet_no)
        # len(pallet_map) 은 distinct pallet_no 의 갯수라 비어있는 팔레트가 있을 시 부정확
        _pallet_count = (
            int(plan.total_pallets) if plan.total_pallets
            else (max(pallet_map.keys()) if pallet_map else 0)
        )
        pa = PalletAssignment(
            pallets=[pallet_map[k] for k in sorted(pallet_map.keys())],
            total_boxes=sum(e.boxes for p in pallet_map.values() for e in p),
            pallet_count=_pallet_count,
        )
    else:
        pa_items = [
            PA_PalletItem(
                key=it.coupang_option_id,
                name=it.product_name or "",
                boxes=(it.inbound_qty_final or 0) // max(it.box_qty or 1, 1),
            )
            for it in items
            if (it.inbound_qty_final or 0) // max(it.box_qty or 1, 1) > 0
        ]
        pa = pa_assign_pallets(pa_items, pallet_size=cfg.pallet_size_boxes)

    # ─── ③ 쿠팡 결과물 PDF 업로드 + 검수 ──────────────────
    st.subheader("② 쿠팡 입고생성 결과물 검수")
    _is_parcel_now = (plan.shipment_type or 'milkrun') == 'parcel'
    if _is_parcel_now:
        section_note(
            "쿠팡 결과물 PDF 업로드 — <b>바코드 라벨</b> + <b>부착문서</b> 필수. "
            "<b>동봉문서는 박스 내 복수 SKU 혼적 시에만 필요</b> (혼적 미운영 시 미업로드)."
        )
    else:
        section_note(
            "쿠팡 결과물 PDF 3종을 업로드하세요. "
            "바코드 라벨 다운로드 시 소비기한 표기 체크 필수 (번들 상품만 적용)."
        )

    pdf_up = st.file_uploader(
        "쿠팡 입고생성 결과물 PDF (3개 이내)",
        type=["pdf"], accept_multiple_files=True,
        key=f"pkg_{brand}_pdf_{plan.id}",
    )

    label_pdf = attach_pdf = invoice_pdf = None
    # *_fresh = 사용자가 이번 렌더에 신규 업로드한 것 (DB fallback과 구분)
    label_pdf_fresh = attach_pdf_fresh = invoice_pdf_fresh = False
    for f in (pdf_up or []):
        nm = f.name.lower()
        if "label" in nm or "barcode" in nm:
            label_pdf = f
            label_pdf_fresh = True
        elif "물류부착" in f.name or "부착문서" in f.name:
            attach_pdf = f
            attach_pdf_fresh = True
        elif "물류동봉" in f.name or "동봉문서" in f.name:
            invoice_pdf = f
            invoice_pdf_fresh = True

    # DB fallback
    if not label_pdf and "label_pdf" in plan_files:
        n, b = plan_files["label_pdf"]
        label_pdf = io.BytesIO(b)
        label_pdf.name = n
    if not attach_pdf and "attach_pdf" in plan_files:
        n, b = plan_files["attach_pdf"]
        attach_pdf = io.BytesIO(b)
        attach_pdf.name = n
    if not invoice_pdf and "invoice_pdf" in plan_files:
        n, b = plan_files["invoice_pdf"]
        invoice_pdf = io.BytesIO(b)
        invoice_pdf.name = n

    # PDF 상태 — 이전 업로드된 파일이 있으면 명시
    def _pdf_disp(name, file_obj, db_key, optional=False):
        if file_obj is None:
            if optional:
                return f"— {name} 미업로드 (선택)"
            return f"❌ {name} 미업로드"
        fname = getattr(file_obj, 'name', '?')
        prev = (db_key in plan_files
                and not (pdf_up and any(
                    db_key.replace("_pdf", "") in (f.name or '').lower()
                    or "물류부착" in (f.name or '') and db_key == "attach_pdf"
                    or "물류동봉" in (f.name or '') and db_key == "invoice_pdf"
                    for f in pdf_up
                )))
        src = " (이전 저장됨)" if prev else " (방금 업로드)"
        return f"✅ {name}: `{fname}`{src}"

    # 바코드 라벨: 단품만 납품 시 발생하지 않음 → 선택
    # 동봉문서: 밀크런 필수 / 택배는 옵션 (혼적 시만 필요, 운영 상 미운영)
    st.caption("📎 PDF 상태:")
    for line in [
        _pdf_disp("바코드 라벨", label_pdf, "label_pdf", optional=True),
        _pdf_disp("부착 문서", attach_pdf, "attach_pdf"),
        _pdf_disp("동봉 문서", invoice_pdf, "invoice_pdf", optional=_is_parcel_now),
    ]:
        st.caption(line)

    if not attach_pdf:
        if _is_parcel_now:
            st.info("부착 문서 PDF 업로드 필수. 바코드 라벨은 단품만이면 없을 수 있음(선택). "
                    "동봉 문서는 혼적 박스 있을 때만 (택배는 보통 미운영).")
        else:
            st.info("부착 문서 + 동봉 문서 PDF 업로드 필수 (밀크런). 바코드 라벨은 단품만이면 없을 수 있음(선택).")
        return

    # 밀크런: 동봉 문서도 필수
    if not _is_parcel_now and not invoice_pdf:
        st.warning("⚠️ 밀크런은 동봉 문서 PDF 도 필수 — 업로드 후 진행 가능.")
        return

    # 바코드 라벨은 선택(단품만이면 발생 안 함) — None-safe 처리
    lb = lname = None
    if label_pdf is not None:
        lb = label_pdf.getvalue() if hasattr(label_pdf, 'getvalue') else label_pdf.read()
        lname = getattr(label_pdf, 'name', 'label.pdf')
    ab = attach_pdf.getvalue() if hasattr(attach_pdf, 'getvalue') else attach_pdf.read()
    aname = getattr(attach_pdf, 'name', 'attach.pdf')
    ib = None
    iname = None
    if invoice_pdf:
        ib = invoice_pdf.getvalue() if hasattr(invoice_pdf, 'getvalue') else invoice_pdf.read()
        iname = getattr(invoice_pdf, 'name', 'invoice.pdf')

    # PDF 저장 정책:
    #   최초 (DB 미존재) — 신규 업로드 시 즉시 저장
    #   교체 (DB 존재)   — 즉시 저장하지 않고, 아래 "변경하기" 결정 시까지 보류
    #                     (혹은 차이 없으면 그대로 두고 새 파일은 메모리에서만 사용)
    new_pdfs: dict[str, tuple[str, bytes]] = {}
    if lb and label_pdf_fresh and "label_pdf" not in plan_files:
        new_pdfs["label_pdf"] = (lname, lb)
    if attach_pdf_fresh and "attach_pdf" not in plan_files:
        new_pdfs["attach_pdf"] = (aname, ab)
    if ib and invoice_pdf_fresh and "invoice_pdf" not in plan_files:
        new_pdfs["invoice_pdf"] = (iname, ib)
    if new_pdfs:
        save_plan_files(plan.id, new_pdfs)

    # 교체 후보 — 이미 PlanFile 에 있는 항목을 신규로 덮어쓰기 위함.
    # "변경하기" 클릭 시 한꺼번에 save_plan_files 로 갱신.
    _pending_replace_pdfs: dict[str, tuple[str, bytes]] = {}
    if lb and label_pdf_fresh and "label_pdf" in plan_files:
        _pending_replace_pdfs["label_pdf"] = (lname, lb)
    if attach_pdf_fresh and "attach_pdf" in plan_files:
        _pending_replace_pdfs["attach_pdf"] = (aname, ab)
    if ib and invoice_pdf_fresh and "invoice_pdf" in plan_files:
        _pending_replace_pdfs["invoice_pdf"] = (iname, ib)

    # 라벨 없으면 빈 dict — 다운스트림 .get(bc) 안전, label-기대 SKU 시만 경고
    labels_parsed = parse_barcode_labels(lb) if lb else {}
    # 운송방식별 부착문서 파서 분기
    if (plan.shipment_type or 'milkrun') == 'parcel':
        attachment = parse_parcel_attachment_doc(ab)
    else:
        attachment = parse_attachment_doc(ab)
    invoice = parse_invoice_doc(ib) if ib else None

    # 택배: 부착문서에서 추출한 FC 정보가 DB 에 있는지 확인 → 없으면 입력 받음
    if (
        (plan.shipment_type or 'milkrun') == 'parcel'
        and attachment.fc_name
    ):
        _fc_info = get_fc_info(attachment.fc_name)
        if _fc_info is None:
            st.warning(
                f"⚠️ FC **{attachment.fc_name}** 정보가 등록되어 있지 않습니다. "
                "아래 정보를 입력하고 저장해 주세요. (저장 후 검수 계속 진행)"
            )
            with st.form(f"fc_info_form_{plan.id}_{attachment.fc_name}"):
                fc1, fc2, fc3 = st.columns([2, 1, 1])
                with fc1:
                    new_addr = st.text_input("주소", placeholder="경기 안산시 ...")
                with fc2:
                    new_zip = st.text_input("우편번호", placeholder="15500")
                with fc3:
                    new_phone = st.text_input("전화번호", placeholder="031-...")
                if st.form_submit_button(f"💾 FC '{attachment.fc_name}' 정보 저장", type="primary"):
                    if new_addr.strip() and new_zip.strip() and new_phone.strip():
                        try:
                            upsert_fc_info(
                                attachment.fc_name,
                                address=new_addr.strip(),
                                postal_code=new_zip.strip(),
                                phone=new_phone.strip(),
                                fc_code=getattr(attachment, 'fc_code', None),
                            )
                            st.success(f"✅ FC '{attachment.fc_name}' 정보 저장 완료")
                            st.rerun()
                        except Exception as ex:
                            st.error(f"FC 정보 저장 실패: {ex}")
                    else:
                        st.error("주소/우편번호/전화번호 모두 입력 필요")
            return  # FC 정보 입력 전엔 검수 진행 차단

    # 메타 입력 UI 가 제거됨 — 첨부문서 파싱 결과로 자동 보정
    if attachment.fc_name:
        meta['fc_name'] = attachment.fc_name
    if attachment.arrival_date:
        meta['arrival_date'] = attachment.arrival_date
    # milkrun_id 결정:
    #   택배: 부착문서 itr_id (요청ID, 예: 131139976)
    #   밀크런: 거래명세서 order_id (발주번호, 예: 128907348)
    if (plan.shipment_type or 'milkrun') == 'parcel':
        _derived_milkrun_id = getattr(attachment, 'itr_id', None)
    else:
        _derived_milkrun_id = invoice.order_id if invoice and invoice.order_id else None
    if _derived_milkrun_id:
        meta['milkrun_id'] = _derived_milkrun_id

    # 첨부 파싱 결과 vs plan 비교 — 첫 설정/변경 케이스 구분.
    #   첫 설정 (plan 값 없음)         → 자동 반영 (신규 발주 흐름 유지)
    #   변경 (plan 값 있고 attachment 와 다름)
    #                                  → 사용자에게 "변경하기/무시" 명시 확인 (자동 덮어쓰기 금지)
    _first_time_fields: dict = {}
    _changed_fields: dict = {}
    with get_session() as ps:
        pdb_ctx = ps.get(InboundPlan, plan.id)
        if attachment.fc_name:
            if not pdb_ctx.fc_name:
                _first_time_fields['fc_name'] = attachment.fc_name
            elif pdb_ctx.fc_name != attachment.fc_name:
                _changed_fields['fc_name'] = (pdb_ctx.fc_name, attachment.fc_name)
        if attachment.arrival_date:
            if not pdb_ctx.arrival_date:
                _first_time_fields['arrival_date'] = attachment.arrival_date
            elif pdb_ctx.arrival_date != attachment.arrival_date:
                _changed_fields['arrival_date'] = (pdb_ctx.arrival_date, attachment.arrival_date)
        if _derived_milkrun_id:
            if not pdb_ctx.milkrun_id:
                _first_time_fields['milkrun_id'] = _derived_milkrun_id
            elif pdb_ctx.milkrun_id != _derived_milkrun_id:
                _changed_fields['milkrun_id'] = (pdb_ctx.milkrun_id, _derived_milkrun_id)

    # 첫 설정 — 자동 반영 후 rerun
    if _first_time_fields:
        with get_session() as ps:
            pdb_ctx = ps.get(InboundPlan, plan.id)
            for k, v in _first_time_fields.items():
                setattr(pdb_ctx, k, v)
            ps.commit()
        st.rerun()

    # 변경 감지 — 명시 확인 UI
    if _changed_fields:
        _diff_sig = tuple(sorted(
            (k, str(old), str(new)) for k, (old, new) in _changed_fields.items()
        ))
        _ignore_key = f"pkg_{brand}_ignore_diff_{plan.id}"
        _ignored_sig = st.session_state.get(_ignore_key)

        if _ignored_sig != _diff_sig:
            st.warning("⚠️ 부착문서 내용이 기존 발주와 다릅니다")
            _labels = {'fc_name': 'FC', 'arrival_date': '입고일', 'milkrun_id': 'milkrun_id'}
            for k, (old, new) in _changed_fields.items():
                st.markdown(f"  - **{_labels.get(k, k)}**: `{old}` → `{new}`")
            c_apply, c_ignore = st.columns(2)
            with c_apply:
                if st.button(
                    "✅ 새 부착문서대로 변경",
                    key=f"pkg_{brand}_apply_change_{plan.id}",
                    type="primary", width="stretch",
                    help="발주의 FC/입고일/milkrun을 새 부착문서 값으로 갱신하고 PDF도 교체합니다.",
                ):
                    with get_session() as ps:
                        pdb_ctx = ps.get(InboundPlan, plan.id)
                        for k, (old, new) in _changed_fields.items():
                            setattr(pdb_ctx, k, new)
                        # 이미 입고확정 이후 plan 이면 변경 이력을 결과로그에 1줄 추가
                        if (pdb_ctx.status or "") in (
                            "inbound_confirmed", "verified", "completed",
                        ):
                            ps.add(CoupangResultLog(
                                company_name=brand_company,
                                milkrun_id=pdb_ctx.milkrun_id or "",
                                fc_name=pdb_ctx.fc_name or "",
                                arrival_date=pdb_ctx.arrival_date,
                                total_pallets=pdb_ctx.total_pallets,
                                total_boxes=None,
                                total_skus=None,
                                plan_id=plan.id,
                                label_filename=None,
                                attachment_filename=aname,
                            ))
                        ps.commit()
                    # PlanFile 도 신규 업로드 파일로 교체 (보류분이 있으면)
                    if _pending_replace_pdfs:
                        save_plan_files(plan.id, _pending_replace_pdfs)
                    st.session_state.pop(_ignore_key, None)
                    st.success("✅ 발주 변경 반영됨")
                    st.rerun()
            with c_ignore:
                if st.button(
                    "↩ 무시 (기존 유지)",
                    key=f"pkg_{brand}_ignore_change_{plan.id}",
                    width="stretch",
                    help="기존 등록된 FC/입고일/milkrun을 유지합니다. 신규 업로드 PDF는 DB에 저장하지 않고 메모리에서만 사용.",
                ):
                    st.session_state[_ignore_key] = _diff_sig
                    st.rerun()
            return  # 결정 전엔 하단 검수/확정 UI 차단
        else:
            # 무시 선택됨 — meta 를 plan 기존값으로 되돌려 다운스트림 일관성 유지
            if 'fc_name' in _changed_fields and plan.fc_name:
                meta['fc_name'] = plan.fc_name
            if 'arrival_date' in _changed_fields and plan.arrival_date:
                meta['arrival_date'] = plan.arrival_date
            if 'milkrun_id' in _changed_fields and plan.milkrun_id:
                meta['milkrun_id'] = plan.milkrun_id
                _derived_milkrun_id = plan.milkrun_id
            st.info(
                f"ℹ️ 부착문서 차이를 무시하고 기존 발주를 유지: "
                f"FC=`{plan.fc_name}` · 입고=`{plan.arrival_date}` · milkrun=`{plan.milkrun_id}`. "
                "되돌리려면 부착문서 PDF를 다시 업로드하세요."
            )

    # PlannedSku 빌드
    planned: list[PlannedSku] = []
    for it in items:
        cm = cp_master_by_opt.get(it.coupang_option_id)
        own = cm.wms_barcode if cm else None
        cbc = cm.coupang_barcode if cm else None
        pbc, uq = resolve_parent_barcode(cm, wms_master_by_bc, wms_master_by_opt) if cm else (None, 1)
        wp = wms_master_by_bc.get(own) if own else None
        pwp = wms_master_by_bc.get(pbc) if pbc else None
        shl = (wp.shelf_life_days if wp else None) or (pwp.shelf_life_days if pwp else None)
        bq = it.box_qty or 1
        boxes = (it.inbound_qty_final or 0) // max(bq, 1)
        emfg = None
        if it.wms_short_expiry and shl:
            emfg = it.wms_short_expiry - timedelta(days=int(shl) - 1)
        # 상품명 = WMS 제품명 (own → parent → 쿠팡 폴백)
        _wms_name = (
            (wp.product_name if wp and wp.product_name else None)
            or (pwp.product_name if pwp and pwp.product_name else None)
        )
        _display_name = _wms_name or (cm.product_name if cm else it.product_name)
        planned.append(PlannedSku(
            coupang_option_id=it.coupang_option_id,
            sku_id=cm.sku_id if cm else None,
            product_name=_display_name,
            option_name=cm.option_name if cm else it.option_name,
            own_wms_barcode=own,
            parent_wms_barcode=pbc, unit_qty=uq,
            coupang_barcode=cbc,
            inbound_qty=it.inbound_qty_final or 0,
            box_qty=bq, boxes=boxes,
            expects_label=False,
            expected_attached_barcode=None,
            expected_expiry=it.wms_short_expiry,
            expected_manufacture=emfg,
        ))

    # 중복 체크 (운송별 식별 ID 기준 — 밀크런: 발주번호, 택배: 요청ID)
    duplicate = False
    if _derived_milkrun_id:
        with get_session() as ds:
            dups = ds.execute(select(CoupangResultLog).where(
                CoupangResultLog.milkrun_id == _derived_milkrun_id,
                CoupangResultLog.company_name == brand_company,
            )).scalars().all()
            existing_ids = {d.plan_id for d in dups}
            if dups and plan.id not in existing_ids:
                duplicate = True
                st.warning(
                    f"⚠️ ID {_derived_milkrun_id} 이미 처리된 이력 있음 — 다른 plan."
                )

    # 검수 실행
    mvt_total = None
    if plan.movement_template_blob:
        mvt_total = sum(
            s.inbound_qty for s in planned
            if s.unit_qty and s.unit_qty >= 2 and s.inbound_qty > 0
        )
    report = verify(
        planned_skus=planned,
        labels=labels_parsed,
        attachment=attachment,
        pallet_assignment=pa,
        duplicate_check=duplicate,
        movement_inbound_total=mvt_total,
        invoice=invoice,
    )
    # 검수 항목 필터/리네임
    _STATUS_ICON = {"ok": "✅", "warning": "⚠️", "fail": "❌"}
    _PARCEL_RENAME = {
        # 택배 — 라벨 관련 3항목
        "라벨 추가(잘못 들어감)": "상품 일치",
        "라벨 누락": "번들 라벨 인쇄",
        "라벨 소비기한 표기": "번들 라벨 소비기한 포함",
    }
    _MILKRUN_RENAME = {
        # 밀크런 — 6항목 (원본 프로젝트 동일)
        "거래명세서 SKU별 수량 일치": "발주수량 일치",
        "팔레트수 일치": "팔레트수 일치",
        "총 박스수": "박스수 일치",
        "거래명세서 소비기한 일치": "소비기한 일치",
        "라벨 누락": "라벨 인쇄 여부",
        "라벨 소비기한 표기": "라벨 소비기한 일치",
    }
    if _is_parcel_now:
        _rename_map = _PARCEL_RENAME
    else:
        _rename_map = _MILKRUN_RENAME
    _checks_pairs = [
        (_rename_map[c.name], c)
        for c in report.checks if c.name in _rename_map
    ]
    _checks = [c for _, c in _checks_pairs]

    # overall 재계산 (필터된 체크 기준)
    _statuses = [c.status for c in _checks]
    if any(s == "fail" for s in _statuses):
        _effective_overall = "fail"
    elif any(s == "warning" for s in _statuses):
        _effective_overall = "warning"
    else:
        _effective_overall = "ok"

    if _effective_overall == "ok":
        st.success("✅ 검수 통과")
    elif _effective_overall == "warning":
        st.warning("⚠️ 일부 항목 확인 필요")
    else:
        st.error("❌ 검수 실패")

    # 요약 (2 cols: 항목 / 일치 여부)
    st.markdown("##### 요약")
    summary_rows = [
        {
            "항목": display_name,
            "일치 여부": _STATUS_ICON.get(chk.status, "?"),
        }
        for display_name, chk in _checks_pairs
    ]
    st.dataframe(
        pd.DataFrame(summary_rows),
        width="stretch", hide_index=True,
        column_config={
            "항목": st.column_config.TextColumn("항목", width="medium"),
            "일치 여부": st.column_config.TextColumn("일치 여부", width="small"),
        },
    )

    # 상세 헤더
    st.markdown("##### 상세")

    # SKU 별 검수 결과 — 거래명세서 매칭 인덱스
    inv_by_bc: dict[str, Any] = {}
    inv_by_sku: dict[str, Any] = {}
    if invoice and invoice.items:
        inv_by_bc = {it.barcode: it for it in invoice.items if it.barcode}
        inv_by_sku = {str(it.sku_id): it for it in invoice.items if it.sku_id}

    def _match_invoice(sku: PlannedSku):
        if sku.sku_id and str(sku.sku_id) in inv_by_sku:
            return inv_by_sku[str(sku.sku_id)]
        for bc in (sku.coupang_barcode, sku.own_wms_barcode):
            if bc and bc in inv_by_bc:
                return inv_by_bc[bc]
        return None

    check_rows: list[dict[str, Any]] = []
    for sku in planned:
        inv_match = _match_invoice(sku)
        bc, _ = derive_attached_barcode(sku)
        expects_label = is_label_expected(sku)
        label_info = labels_parsed.get(bc) if bc else None
        name_ok = (inv_match is not None) if (invoice and invoice.items) else None
        qty_ok = (inv_match.confirmed_qty == sku.inbound_qty) if inv_match else None
        exp_ok = None
        if inv_match and inv_match.expiry and sku.expected_expiry:
            exp_ok = (inv_match.expiry == sku.expected_expiry)
        # 라벨 인쇄 여부 (단순 존재)
        label_present_ok: Any = "—" if not expects_label else (label_info is not None)
        # 라벨 수량 일치 (count == inbound_qty)
        if not expects_label:
            label_count_ok: Any = "—"
            label_count_actual: Any = "—"
        elif label_info is None:
            label_count_ok = False
            label_count_actual = 0
        else:
            label_count_ok = (label_info.count == sku.inbound_qty)
            label_count_actual = label_info.count
        # 라벨 소비기한 일치
        if not expects_label:
            label_exp_ok: Any = "—"
        elif label_info is None or label_info.expiry is None:
            label_exp_ok = False
        else:
            label_exp_ok = (label_info.expiry == sku.expected_expiry)

        if _is_parcel_now:
            # 택배: 라벨 정보 위주 (수량/소비기한 검증은 거래명세서 없어 생략)
            check_rows.append({
                "옵션ID": sku.coupang_option_id,
                "SKU ID": sku.sku_id,
                "상품명": sku.product_name or "",
                "수량": sku.inbound_qty,
                "소비기한": sku.expected_expiry,
                "라벨 인쇄": "—" if label_present_ok == "—" else ("✅" if label_present_ok else "❌"),
                "라벨 수량 일치": "—" if label_count_ok == "—" else ("✅" if label_count_ok else "❌"),
                "라벨 소비기한": "—" if label_exp_ok == "—" else ("✅" if label_exp_ok else "❌"),
            })
        else:
            check_rows.append({
                "옵션ID": sku.coupang_option_id,
                "SKU ID": sku.sku_id,
                "상품명": sku.product_name or "",
                "수량": sku.inbound_qty,
                "소비기한": sku.expected_expiry,
                "거래명세서 수량": inv_match.confirmed_qty if inv_match else None,
                "상품일치": "✅" if name_ok else ("—" if name_ok is None else "❌"),
                "발주수량": "✅" if qty_ok else ("—" if qty_ok is None else "❌"),
                "소비기한 일치": "✅" if exp_ok else ("—" if exp_ok is None else "❌"),
                "라벨 인쇄": "✅" if label_count_ok is True else ("—" if label_count_ok == "—" else "❌"),
                "라벨 소비기한": "✅" if label_exp_ok is True else ("—" if label_exp_ok == "—" else "❌"),
            })

    if _is_parcel_now:
        _detail_cfg = {
            "옵션ID": st.column_config.NumberColumn("옵션ID", format="%d"),
            "SKU ID": st.column_config.NumberColumn("SKU ID", format="%d"),
            "상품명": st.column_config.TextColumn("상품명", width="large"),
            "수량": st.column_config.NumberColumn("수량", format="%d"),
            "소비기한": st.column_config.DateColumn("소비기한", format="YYYY-MM-DD"),
        }
    else:
        _detail_cfg = {
            "옵션ID": st.column_config.NumberColumn("옵션ID", format="%d"),
            "SKU ID": st.column_config.NumberColumn("SKU ID", format="%d"),
            "상품명": st.column_config.TextColumn("상품명", width="large"),
            "수량": st.column_config.NumberColumn("수량", format="%d"),
            "소비기한": st.column_config.DateColumn("소비기한", format="YYYY-MM-DD"),
            "거래명세서 수량": st.column_config.NumberColumn("거래명세서 수량", format="%d"),
        }
    st.dataframe(
        pd.DataFrame(check_rows),
        width="stretch", hide_index=True,
        column_config=_detail_cfg,
    )

    # ─── 입고생성 확정 + 다음 단계 (탭 2 마지막, 검수 통과 시 노출) ───
    st.divider()

    # 검수 통과(ok 또는 warning) 일 때만 버튼 영역 노출. fail 이면 숨김.
    _verification_passed = _effective_overall in ("ok", "warning")

    if not _verification_passed:
        st.warning("❌ 검수 실패 — 위 검수 이슈를 해결한 후 입고생성 확정을 진행할 수 있습니다.")
    else:
        already_confirmed = (plan.status or "") in (
            "inbound_confirmed", "verified", "completed"
        )
        btn_cols = st.columns(2)
        with btn_cols[0]:
            if already_confirmed:
                st.button(
                    "✅ 입고생성 확정됨",
                    disabled=True, width="stretch",
                    help=f"plan #{plan.id} 입고확정 완료 — 수량 수정 불가.",
                    key=f"pkg_{brand}_inbound_done_{plan.id}",
                )
            else:
                if st.button(
                    "입고생성 확정",
                    type="primary", width="stretch",
                    help="검수 결과 OK 시 활성화. 클릭 시 status=inbound_confirmed 로 변경 + CoupangResultLog 기록 + 수량 잠금.",
                    key=f"pkg_{brand}_inbound_confirm_{plan.id}",
                ):
                    try:
                        with get_session() as s4:
                            pdb = s4.get(InboundPlan, plan.id)
                            pdb.status = "inbound_confirmed"
                            pdb.fc_name = meta['fc_name']
                            pdb.worker = meta['worker']
                            pdb.arrival_date = meta['arrival_date']
                            pdb.milkrun_id = meta['milkrun_id'] or _derived_milkrun_id or None
                            pdb.shipment_type = meta['shipment_type']
                            pdb.total_pallets = pa.pallet_count if meta['shipment_type'] == 'milkrun' else None
                            items_by_opt = {it.coupang_option_id: it for it in s4.execute(
                                select(InboundPlanItem).where(InboundPlanItem.plan_id == plan.id)
                            ).scalars().all()}
                            for pi, pal in enumerate(pa.pallets, start=1):
                                for en in pal:
                                    dbi = items_by_opt.get(en.key)
                                    if dbi:
                                        sk = next((s for s in planned if s.coupang_option_id == en.key), None)
                                        if sk:
                                            cm7 = cp_master_by_opt.get(sk.coupang_option_id)
                                            bc7 = (
                                                cm7.coupang_barcode if cm7 and cm7.coupang_barcode
                                                and cm7.coupang_barcode.startswith("S0")
                                                else sk.own_wms_barcode
                                            )
                                            bt7 = (
                                                "쿠팡바코드"
                                                if (cm7 and cm7.coupang_barcode and cm7.coupang_barcode.startswith("S0"))
                                                else "88코드"
                                            )
                                            dbi.pallet_no = pi
                                            dbi.barcode_attached = bc7
                                            dbi.barcode_type = bt7
                            tb = sum(s.boxes for s in planned)
                            s4.add(CoupangResultLog(
                                company_name=brand_company,
                                milkrun_id=_derived_milkrun_id or "",
                                fc_name=meta['fc_name'], arrival_date=meta['arrival_date'],
                                total_pallets=pa.pallet_count, total_boxes=tb,
                                total_skus=len([s for s in planned if s.boxes > 0]),
                                plan_id=plan.id,
                                label_filename=lname, attachment_filename=aname,
                            ))
                            s4.commit()
                        st.success(f"✅ 입고확정 (plan #{plan.id}). 수량 잠금됨.")
                        st.rerun()
                    except Exception as ex:
                        st.error(f"입고확정 실패: {ex}")
        with btn_cols[1]:
            if st.button(
                "다음 단계 →",
                disabled=(not already_confirmed),
                type="primary" if already_confirmed else "secondary",
                width="stretch",
                help="물류센터 출고 요청 탭으로 이동.",
                key=f"pkg_{brand}_goto_dispatch_{plan.id}",
            ):
                st.session_state[f"rg_{brand}_pending_dispatch_pick"] = plan.id
                jump_to_tab(2)

