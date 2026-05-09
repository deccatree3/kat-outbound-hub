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
)
from rocketgrowth.db import get_session
from rocketgrowth.models import (
    CoupangProduct, CoupangResultLog, InboundPlan, InboundPlanItem, PlanFile, WmsProduct,
)
from rocketgrowth.outbound import PoolAllocationItem, allocate_parent_pool
from rocketgrowth.pallet_assign import (
    PalletAssignment, PalletEntry, PalletItem as PA_PalletItem, assign_pallets as pa_assign_pallets,
)
from rocketgrowth.secondary_export import (
    SecondaryItem, build_consolidation_list, build_order_form, build_pallet_loading_list,
    update_inventory_movement,
)
from outputs.daone.builder import build_daone_xlsx
from rocketgrowth.verification import (
    PlannedSku, derive_attached_barcode, is_label_expected, verify,
)

from channels.rocketgrowth._helpers import (
    STATUS_LABELS, derive_substatus_label, load_plan_files, resolve_parent_barcode,
    save_plan_files, section_note,
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
    worker = plan.worker or "미정"
    milkrun = plan.milkrun_id or "미정"
    parts = [
        f'<span style="background:#fef3c7; color:#92400e; padding:3px 8px; '
        f'border-radius:4px; font-weight:700;">{sid}</span>',
        f'<span>{status_label}</span>',
        f'<span><b>업체</b> {company}</span>',
        f'<span><b>FC</b> {fc}</span>',
        f'<span><b>입고일</b> {arr}</span>',
        f'<span><b>작업자</b> {worker}</span>',
        f'<span><b>milkrun_id</b> {milkrun}</span>',
    ]
    return (
        '<div style="display:flex; flex-wrap:wrap; gap:12px; align-items:center; '
        'padding:8px 12px; background:#f9fafb; border:1px solid #e5e7eb; '
        'border-radius:6px; margin:0 0 10px 0; font-size:0.92em;">'
        + "".join(parts) + "</div>"
    )


def _select_plan(brand_company: str) -> InboundPlan | None:
    """업체별 plan dropdown — 방금 저장한 plan_id (session) 우선 자동 선택."""
    with get_session() as s:
        plans = s.execute(
            select(InboundPlan)
            .where(InboundPlan.company_name == brand_company)
            .order_by(desc(InboundPlan.created_at))
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

    options = [
        f"#{p.id} {derive_substatus_label(p, has_attach_pdf=(p.id in has_attach))} · "
        f"{p.company_name} · {p.arrival_date or p.plan_date or ''}"
        + (f" · {p.fc_name}" if p.fc_name else "")
        for p in plans
    ]

    # 방금 저장한 plan 자동 선택
    auto_plan_id = None
    for k in (f'rg_nenu_last_saved_plan_id', f'rg_cachers_last_saved_plan_id'):
        if st.session_state.get(k):
            cand_id = st.session_state[k]
            if any(p.id == cand_id for p in plans):
                auto_plan_id = cand_id
                break

    default_idx = 0
    if auto_plan_id is not None:
        for i, p in enumerate(plans):
            if p.id == auto_plan_id:
                default_idx = i
                break

    sel = st.selectbox(
        "발주 계획 선택",
        options=range(len(plans)),
        format_func=lambda i: options[i],
        index=default_idx,
        key=f"pkg_{brand_company}_plan_select",
    )
    return plans[sel]


SHIPMENT_LABELS = {'milkrun': '밀크런', 'parcel': '택배'}


def _derive_meta(plan: InboundPlan) -> dict[str, Any]:
    """plan 레코드에서 메타 자동 derive — 입력 UI 제거 후 사용.

    fc_name / milkrun_id / arrival_date 는 검수 단계에서 첨부문서 파싱 결과로
    추후 보정될 수 있음 (verify_section 내부에서 attachment.fc / attachment.milkrun_id /
    attachment.arrival_date 사용).
    """
    cfg = load_config()
    return {
        'fc_name': plan.fc_name or "동탄1",
        'worker': plan.worker or cfg.default_company_name,
        'arrival_date': plan.arrival_date or plan.plan_date or _date.today(),
        'milkrun_id': plan.milkrun_id,
        'shipment_type': plan.shipment_type or 'milkrun',
    }


def render(brand: str):
    """탭 2 메인."""
    cfg = load_config()
    brand_company = _BRAND_TO_COMPANY[brand]

    plan = _select_plan(brand_company)
    if plan is None:
        return

    plan_files = load_plan_files(plan.id)
    st.markdown(
        _render_context_bar(plan, has_attach_pdf=("attach_pdf" in plan_files)),
        unsafe_allow_html=True,
    )

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

    # ─── ② 쿠팡 입고생성 계획 요약 ───────────────────
    import math as _math
    st.subheader("① 쿠팡 입고생성 계획 요약")
    section_note("아래 계획대로 Wing에서 입고생성을 해주세요.")

    # 메트릭 — 박스수/팔레트 ceil 기반 (탭 1 과 동일)
    total_qty = int(sum(int(i.inbound_qty_final or 0) for i in items))
    total_boxes = int(sum(
        _math.ceil((i.inbound_qty_final or 0) / max(int(i.box_qty or 1), 1))
        for i in items
    ))
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
    plan_df = pd.DataFrame([{
        "상품명": (
            f"{(cp_master_by_opt.get(i.coupang_option_id).product_name if cp_master_by_opt.get(i.coupang_option_id) else (i.product_name or ''))} "
            f"{(cp_master_by_opt.get(i.coupang_option_id).option_name if cp_master_by_opt.get(i.coupang_option_id) else (i.option_name or ''))}"
        ).strip(),
        "소비기한": i.wms_short_expiry,
        "상품수": i.inbound_qty_final,
        "박스수": _math.ceil((i.inbound_qty_final or 0) / max(int(i.box_qty or 1), 1)),
    } for i in items])
    st.dataframe(
        plan_df, width="stretch", hide_index=True, height=380,
        column_config={
            "상품명": st.column_config.TextColumn("상품명", width="large"),
            "소비기한": st.column_config.DateColumn("소비기한", format="YYYY-MM-DD"),
            "상품수": st.column_config.NumberColumn("상품수", format="%d"),
            "박스수": st.column_config.NumberColumn("박스수", format="%d"),
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
        pa = PalletAssignment(
            pallets=[pallet_map[k] for k in sorted(pallet_map.keys())],
            total_boxes=sum(e.boxes for p in pallet_map.values() for e in p),
            pallet_count=len(pallet_map),
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
    for f in (pdf_up or []):
        nm = f.name.lower()
        if "label" in nm or "barcode" in nm:
            label_pdf = f
        elif "물류부착" in f.name or "부착문서" in f.name:
            attach_pdf = f
        elif "물류동봉" in f.name or "동봉문서" in f.name:
            invoice_pdf = f

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

    pdf_status = (
        f"바코드 라벨: {'✅' if label_pdf else '❌'} · "
        f"부착 문서: {'✅' if attach_pdf else '❌'} · "
        f"동봉 문서: {'✅' if invoice_pdf else '⚪'}"
    )
    st.caption(pdf_status)

    if not (label_pdf and attach_pdf):
        st.info("바코드 라벨 PDF + 부착 문서 PDF 업로드 필요. 동봉 문서는 혼적 박스 있을 때만.")
        return

    lb = label_pdf.getvalue() if hasattr(label_pdf, 'getvalue') else label_pdf.read()
    ab = attach_pdf.getvalue() if hasattr(attach_pdf, 'getvalue') else attach_pdf.read()
    lname = getattr(label_pdf, 'name', 'label.pdf')
    aname = getattr(attach_pdf, 'name', 'attach.pdf')
    ib = None
    iname = None
    if invoice_pdf:
        ib = invoice_pdf.getvalue() if hasattr(invoice_pdf, 'getvalue') else invoice_pdf.read()
        iname = getattr(invoice_pdf, 'name', 'invoice.pdf')

    # PDF 신규 → DB 저장
    new_pdfs: dict[str, tuple[str, bytes]] = {}
    if "label_pdf" not in plan_files:
        new_pdfs["label_pdf"] = (lname, lb)
    if "attach_pdf" not in plan_files:
        new_pdfs["attach_pdf"] = (aname, ab)
    if ib and "invoice_pdf" not in plan_files:
        new_pdfs["invoice_pdf"] = (iname, ib)
    if new_pdfs:
        save_plan_files(plan.id, new_pdfs)

    labels_parsed = parse_barcode_labels(lb)
    attachment = parse_attachment_doc(ab)
    invoice = parse_invoice_doc(ib) if ib else None

    # 메타 입력 UI 가 제거됨 — 첨부문서 파싱 결과로 자동 보정
    if attachment.fc_name:
        meta['fc_name'] = attachment.fc_name
    if attachment.arrival_date:
        meta['arrival_date'] = attachment.arrival_date
    if attachment.milkrun_id:
        meta['milkrun_id'] = attachment.milkrun_id

    # 첨부 파싱 결과를 plan 에 영구 반영 → 다음 렌더 시 컨텍스트 바 갱신
    _ctx_changed = False
    with get_session() as ps:
        pdb_ctx = ps.get(InboundPlan, plan.id)
        if attachment.fc_name and pdb_ctx.fc_name != attachment.fc_name:
            pdb_ctx.fc_name = attachment.fc_name
            _ctx_changed = True
        if attachment.arrival_date and pdb_ctx.arrival_date != attachment.arrival_date:
            pdb_ctx.arrival_date = attachment.arrival_date
            _ctx_changed = True
        if attachment.milkrun_id and pdb_ctx.milkrun_id != attachment.milkrun_id:
            pdb_ctx.milkrun_id = attachment.milkrun_id
            _ctx_changed = True
        if _ctx_changed:
            ps.commit()
    if _ctx_changed:
        st.rerun()

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
        planned.append(PlannedSku(
            coupang_option_id=it.coupang_option_id,
            sku_id=cm.sku_id if cm else None,
            product_name=cm.product_name if cm else it.product_name,
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

    # 중복 체크 (밀크런 ID 기준)
    duplicate = False
    if attachment.milkrun_id:
        with get_session() as ds:
            dups = ds.execute(select(CoupangResultLog).where(
                CoupangResultLog.milkrun_id == attachment.milkrun_id,
                CoupangResultLog.company_name == brand_company,
            )).scalars().all()
            existing_ids = {d.plan_id for d in dups}
            if dups and plan.id not in existing_ids:
                duplicate = True
                st.warning(
                    f"⚠️ 밀크런 ID {attachment.milkrun_id} 이미 처리된 이력 있음 — 다른 plan."
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
    if report.overall == "ok":
        st.success("✅ 검수 통과")
    elif report.overall == "warning":
        st.warning("⚠️ 일부 항목 확인 필요")
    else:
        st.error("❌ 검수 실패")

    # 검수 요약 — 전체 체크 항목 (원본 프로젝트와 동일)
    _STATUS_ICON = {"ok": "✅", "warning": "⚠️", "fail": "❌"}
    summary_rows = [
        {
            "검수 항목": chk.name,
            "상태": _STATUS_ICON.get(chk.status, "?"),
            "상세": (
                chk.detail or (
                    f"기대 {chk.expected} / 실제 {chk.actual}"
                    if (chk.expected is not None or chk.actual is not None) else ""
                )
            ),
        }
        for chk in report.checks
    ]
    st.dataframe(
        pd.DataFrame(summary_rows),
        width="stretch", hide_index=True,
        column_config={
            "검수 항목": st.column_config.TextColumn("검수 항목", width="medium"),
            "상태": st.column_config.TextColumn("상태", width="small"),
            "상세": st.column_config.TextColumn("상세", width="large"),
        },
    )

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
        if not expects_label:
            label_ok = "—"
        elif label_info is None:
            label_ok = False
        else:
            label_ok = (label_info.count == sku.inbound_qty)
        if not expects_label:
            label_exp_ok = "—"
        elif label_info is None or label_info.expiry is None:
            label_exp_ok = False
        else:
            label_exp_ok = (label_info.expiry == sku.expected_expiry)

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
            "라벨 인쇄": "✅" if label_ok is True else ("—" if label_ok == "—" else "❌"),
            "라벨 소비기한": "✅" if label_exp_ok is True else ("—" if label_exp_ok == "—" else "❌"),
        })

    st.dataframe(
        pd.DataFrame(check_rows),
        width="stretch", hide_index=True,
        column_config={
            "옵션ID": st.column_config.NumberColumn("옵션ID", format="%d"),
            "SKU ID": st.column_config.NumberColumn("SKU ID", format="%d"),
            "상품명": st.column_config.TextColumn("상품명", width="large"),
            "수량": st.column_config.NumberColumn("수량", format="%d"),
            "소비기한": st.column_config.DateColumn("소비기한", format="YYYY-MM-DD"),
            "거래명세서 수량": st.column_config.NumberColumn("거래명세서 수량", format="%d"),
        },
    )

    # ─── 발주 확정 ────────────────────────────────────────
    st.divider()
    if plan.status == "draft":
        if st.button(
            "✅ 발주 확정 (status → verified)",
            type="primary", width="stretch",
            disabled=(report.overall == "fail" or not meta['fc_name']),
            key=f"pkg_{brand}_verify_{plan.id}",
            help="검수 통과 시 활성화. 클릭 시 status=verified 로 변경 + CoupangResultLog 기록.",
        ):
            try:
                with get_session() as s4:
                    pdb = s4.get(InboundPlan, plan.id)
                    pdb.status = "verified"
                    pdb.fc_name = meta['fc_name']
                    pdb.worker = meta['worker']
                    pdb.arrival_date = meta['arrival_date']
                    pdb.milkrun_id = meta['milkrun_id'] or attachment.milkrun_id or None
                    pdb.shipment_type = meta['shipment_type']
                    pdb.total_pallets = pa.pallet_count if meta['shipment_type'] == 'milkrun' else None
                    # 팔레트 번호 + 부착 바코드 db 반영
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
                        milkrun_id=attachment.milkrun_id or "",
                        fc_name=meta['fc_name'], arrival_date=meta['arrival_date'],
                        total_pallets=pa.pallet_count, total_boxes=tb,
                        total_skus=len([s for s in planned if s.boxes > 0]),
                        plan_id=plan.id,
                        label_filename=lname, attachment_filename=aname,
                    ))
                    s4.commit()
                st.success(f"✅ 발주 #{plan.id} 확정 완료")
                st.rerun()
            except Exception as ex:
                st.error(f"확정 실패: {ex}")
    elif plan.status == "verified":
        st.success(f"✅ 발주 확정됨 (plan_id={plan.id}). 아래 ④ 물류센터 전달 파일 다운로드.")
    else:
        st.info(f"plan status: {plan.status}")

    # ─── ④ 물류센터 전달 파일 ──────────────────────────────
    if plan.status not in ("verified", "completed"):
        return

    shipment_type = plan.shipment_type or 'milkrun'
    is_milkrun = shipment_type == 'milkrun'
    ship_label = SHIPMENT_LABELS.get(shipment_type, shipment_type)

    st.subheader(f"③ 물류센터 전달 파일 ({ship_label})")
    if is_milkrun:
        section_note(
            "아래 파일 다운로드 → 메일 송부.<br>"
            "<b>밀크런</b>: 팔레트 단위 → 팔레트적재리스트 포함."
        )
    else:
        section_note(
            "아래 파일 다운로드 → 메일 송부.<br>"
            "<b>택배</b>: 박스 단위 → 팔레트적재리스트 제외 (택배 박스 라벨은 후속 단계에서 추가)."
        )

    fc = meta['fc_name']
    arr = meta['arrival_date']
    yymmdd = arr.strftime("%y%m%d") if arr else _date.today().strftime("%y%m%d")
    yyyymm = arr.strftime("%Y_%m월") if arr else _date.today().strftime("%Y_%m월")
    datesuf = arr.strftime("%Y%m%d") if arr else _date.today().strftime("%Y%m%d")
    order_base = (invoice.order_id if invoice and invoice.order_id else None) or (plan.milkrun_id or attachment.milkrun_id or "")
    ship_prefix = "밀크런" if is_milkrun else "택배"

    # 취합리스트 + (밀크런만) 팔레트적재 + 재고이동건
    if is_milkrun:
        dc = st.columns(3)
    else:
        dc = st.columns(2)
    try:
        cons = build_consolidation_list(
            sec_items, pa, fc, arr, brand_company,
            invoice.order_id if invoice and invoice.order_id else attachment.milkrun_id,
        )
        with dc[0]:
            st.download_button(
                "📥 취합리스트", data=cons,
                file_name=f"{brand_company}_{ship_prefix}_취합리스트_{yymmdd}_{fc}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch", type="primary",
                key=f"pkg_{brand}_dl_cons_{plan.id}",
            )
    except Exception as ex:
        with dc[0]:
            st.error(f"취합리스트: {ex}")

    if is_milkrun:
        try:
            pal = build_pallet_loading_list(
                sec_items, pa, fc, arr,
                milkrun_request_id=order_base, pallet_size=cfg.pallet_size_boxes,
            )
            with dc[1]:
                st.download_button(
                    "📥 팔레트적재리스트", data=pal,
                    file_name=f"밀크런_물류부착문서2 (팔레트적재리스트)_{fc}_{datesuf}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch", type="primary",
                    key=f"pkg_{brand}_dl_pal_{plan.id}",
                )
        except Exception as ex:
            with dc[1]:
                st.error(f"팔레트적재: {ex}")
        mv_col = dc[2]
    else:
        mv_col = dc[1]

    if plan.movement_template_blob:
        try:
            mv_out = update_inventory_movement(
                bytes(plan.movement_template_blob), sec_items, arr, fc, brand_company,
            )
            with mv_col:
                st.download_button(
                    "📥 재고이동건", data=mv_out,
                    file_name=plan.movement_template_filename or f"쿠팡 재고이동건_{yyyymm}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch", type="primary",
                    key=f"pkg_{brand}_dl_mv_{plan.id}",
                )
        except Exception as ex:
            with mv_col:
                st.error(f"재고이동건: {ex}")
    else:
        with mv_col:
            st.caption("재고이동건 템플릿 미저장 — 탭 1 에서 업로드 시 활성화")

    # PDF 리네임 다운로드 (운송별 명칭 차이)
    dpc = st.columns(3)
    if ib:
        with dpc[0]:
            st.download_button(
                "📥 물류동봉문서(거래명세서)", data=ib,
                file_name=f"{ship_prefix}_물류동봉문서(거래명세서)_{fc}_{datesuf}.pdf",
                mime="application/pdf", width="stretch", type="primary",
                key=f"pkg_{brand}_dl_inv_{plan.id}",
            )
    else:
        with dpc[0]:
            st.caption("동봉문서 미업로드 (혼적 박스 없는 경우)")
    with dpc[1]:
        st.download_button(
            "📥 제품 바코드라벨", data=lb,
            file_name=f"제품 바코드라벨_{fc}_{datesuf}.pdf",
            mime="application/pdf", width="stretch", type="primary",
            key=f"pkg_{brand}_dl_lb_{plan.id}",
        )
    with dpc[2]:
        attach_label = "팔레트부착" if is_milkrun else "박스부착"
        st.download_button(
            f"📥 물류부착문서({attach_label})", data=ab,
            file_name=f"{ship_prefix}_물류부착문서1 ({attach_label}문서)_{fc}_{datesuf}.pdf",
            mime="application/pdf", width="stretch", type="primary",
            key=f"pkg_{brand}_dl_ab_{plan.id}",
        )

    if not is_milkrun:
        st.info("📦 택배 박스 라벨 출력 양식은 후속 단계에서 추가 예정.")

    # ─── ⑤ 공유시트 기록 (선택) ──────────────────────────────
    st.markdown("##### ④ 공유시트 기록 (선택)")
    section_note(
        "쿠팡 입고생성 후 발급된 입고ID 를 입력하면 공유시트 붙여넣기용 TSV 가 표시됩니다. "
        "Google Sheets 마지막 행 아래에 Ctrl+V — 탭 자동 분할."
    )
    inbound_id = st.text_input(
        "입고ID",
        key=f"pkg_{brand}_inbound_id_{plan.id}",
        help="쿠팡 입고생성 후 발급된 ID",
    )
    if inbound_id.strip():
        from rocketgrowth.secondary_export import build_share_sheet_tsv
        request_d = plan.plan_date or arr
        try:
            ss_tsv = build_share_sheet_tsv(
                sec_items,
                request_date=request_d,
                arrival_date=arr,
                company_short=brand_company,
                inbound_id=inbound_id.strip(),
                pallet_assignment=pa,
            )
            st.caption("아래 박스 우상단 📋 클릭해 복사 → 공유시트에 붙여넣기.")
            st.code(ss_tsv, language=None)
        except Exception as ex:
            st.error(f"공유시트 데이터 생성 실패: {ex}")

    # ─── ⑥ 화주별 출고요청 (네뉴=이지어드민 / 캐처스=다원) ────
    st.markdown(f"##### ⑤ 화주별 출고요청 — **{brand_company}**")
    if brand == 'nenu':
        section_note(
            "네뉴(서현커머스): 이지어드민 발주서양식 다운로드 → 이지어드민 업로드 → "
            "이지어드민↔다원 자동연동으로 다원에 발주 전달."
        )
        try:
            order_xlsx = build_order_form(
                sec_items, fc, str(order_base).strip(),
                pallet_assignment=pa,
            )
            st.download_button(
                "📥 이지어드민 발주서양식",
                data=order_xlsx,
                file_name=(
                    f"{ship_prefix}재고차감_로켓그로스({brand_company}커머스)"
                    f"발주서양식_{datesuf}.xlsx"
                ),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch", type="primary",
                key=f"pkg_{brand}_dl_eaorder_{plan.id}",
            )
        except Exception as ex:
            st.error(f"이지어드민 발주서 생성 실패: {ex}")
    else:
        section_note(
            "캐처스: 다원 출고요청서.xlsx 다운로드 → 다원에 직접 업로드 (수기). "
            "이지어드민 미사용 (캐처스 ↔ 다원 자동연동 없음)."
        )
        try:
            daone_rows = _sec_items_to_daone_rows(
                sec_items, fc, brand_company,
                milkrun_id=order_base or str(plan.id),
                arrival_date=arr,
            )
            if not daone_rows:
                st.info("출고 대상 (inbound_qty > 0) SKU 가 없습니다.")
            else:
                xlsx_bytes = build_daone_xlsx(daone_rows)
                st.download_button(
                    "📥 다원 출고요청서",
                    data=xlsx_bytes,
                    file_name=(
                        f"{ship_prefix}_다원출고요청_로켓그로스(캐처스)_{fc}_{datesuf}.xlsx"
                    ),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch", type="primary",
                    key=f"pkg_{brand}_dl_daone_{plan.id}",
                )
                st.caption(
                    "⚠️ 주문자/수취인 정보는 placeholder — 다원 업로드 전 확인 필요. "
                    "쿠팡 FC 주소 매핑 추가 필요 시 알려주세요."
                )
        except Exception as ex:
            st.error(f"다원 출고요청서 생성 실패: {ex}")

    # 다음 단계 (송장 후처리 탭으로 이동) — 스크롤 없이 탭 전환
    st.divider()
    import streamlit.components.v1 as components
    if st.button(
        "다음 단계 →",
        key=f"pkg_{brand}_goto_invoice",
        type="primary",
        width="stretch",
        help="송장 후처리 탭으로 자동 이동 + 페이지 상단으로 스크롤.",
    ):
        components.html(
            """
            <script>
            const tabs = window.parent.document.querySelectorAll('button[role="tab"]');
            if (tabs.length > 2) {
                tabs[2].click();
                window.parent.scrollTo({top: 0, behavior: 'smooth'});
            }
            </script>
            """,
            height=0,
        )


# ─── 캐처스 다원 출고요청서 생성 helper ──────────────────────
COUPANG_FC_ADDRESS = {
    '동탄1': '경기 화성시 동탄ㅇㅇ로 (placeholder)',
    '화성2': '경기 화성시 화성ㅇㅇ로 (placeholder)',
    '천안2': '충남 천안시 천안ㅇㅇ로 (placeholder)',
    '옥천3': '충북 옥천군 옥천ㅇㅇ로 (placeholder)',
}
COUPANG_FC_PHONE = '02-1577-7011'  # 쿠팡 대표 (placeholder)
CACHERS_INFO = {
    'name': '캐처스',
    'phone1': '02-0000-0000',  # placeholder
    'phone2': '',
}


def _sec_items_to_daone_rows(
    sec_items: list[SecondaryItem],
    fc_name: str,
    brand_company: str,
    milkrun_id: str,
    arrival_date,
) -> list[dict]:
    """SecondaryItem → 다원 19컬럼 dict 리스트.

    캐처스 로켓그로스 → 다원 출고요청 양식.
    주문자 = 캐처스, 수취인 = 쿠팡 FC.
    """
    rows = []
    seq = 0
    for it in sec_items:
        if it.inbound_qty <= 0:
            continue
        seq += 1
        rows.append({
            '몰명(또는 몰코드)': '쿠팡 로켓그로스',
            '출하의뢰번호': f"{milkrun_id}",
            '출하의뢰항번': str(seq),
            '고객주문번호': str(it.coupang_option_id),
            '상품명': it.product_name or '',
            '제품코드': it.own_wms_barcode or '',
            '주문수량': it.inbound_qty,
            '주문자명': CACHERS_INFO['name'],
            '주문자연락처1': CACHERS_INFO['phone1'],
            '주문자연락처2': CACHERS_INFO['phone2'],
            '수취인명': f'쿠팡 {fc_name}',
            '수취인연락처1': COUPANG_FC_PHONE,
            '수취인연락처2': '',
            '수취인우편번호': '',
            '수취인주소1': COUPANG_FC_ADDRESS.get(fc_name, f'쿠팡 {fc_name} (주소 미등록)'),
            '주소2': '',
            '배송메시지': f'쿠팡 로켓그로스 입고 ({arrival_date})' if arrival_date else '쿠팡 로켓그로스 입고',
            '송장번호': '',
            '택배사명': '',
        })
    return rows
