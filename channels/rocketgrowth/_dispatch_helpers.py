"""물류센터 출고 요청 (탭 3) + 화주별 출고요청 (탭 4) 공유 헬퍼.

verified/completed plan 을 선택해 sec_items, pa, fc, arr, PDF bytes 등
탭 3/4 공통 데이터 빌드.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date as _date, timedelta
from typing import Any

import streamlit as st
from sqlalchemy import desc, select

from rocketgrowth.config import load_config
from rocketgrowth.coupang_result import (
    parse_attachment_doc, parse_invoice_doc, parse_parcel_attachment_doc,
)
from rocketgrowth.db import get_session
from rocketgrowth.models import (
    CoupangProduct, InboundPlan, InboundPlanItem, WmsProduct,
)
from rocketgrowth.pallet_assign import (
    PalletAssignment, PalletEntry, PalletItem as PA_PalletItem,
    assign_pallets as pa_assign_pallets,
)
from rocketgrowth.secondary_export import SecondaryItem

from channels.rocketgrowth._helpers import (
    derive_substatus_label, format_plan_label, load_plan_files, resolve_parent_barcode,
)


SHIPMENT_LABELS = {'milkrun': '밀크런', 'parcel': '택배'}
_BRAND_TO_COMPANY = {'nenu': '서현', 'cachers': '캐처스'}


def select_dispatch_plan(brand: str, brand_company: str, key_suffix: str = "") -> InboundPlan | None:
    """입고확정 이상 plan 선택 dropdown — 탭 3/4 공통."""
    with get_session() as s:
        plans = s.execute(
            select(InboundPlan)
            .where(
                InboundPlan.company_name == brand_company,
                InboundPlan.status.in_(['inbound_confirmed', 'verified', 'completed']),
            )
            .order_by(desc(InboundPlan.id))  # # 번호 큰 것 (최근) 이 상단
        ).scalars().all()

    if not plans:
        st.info(
            f"📭 **{brand_company}** 의 입고확정된 plan 이 없습니다. "
            "탭 2 에서 검수 + 입고생성 확정 먼저 진행."
        )
        return None

    SENTINEL = -1
    labels = {SENTINEL: "— 발주계획 선택 —"}
    for i, p in enumerate(plans):
        labels[i] = format_plan_label(p)

    # 다른 탭의 '다음 단계 →' 가 set 한 pending plan 이 있으면 selectbox 에 1회 적용
    # key_suffix='dispatch' (탭 3) / 'invoice' (탭 4) 별로 분리
    sel_key = f"disp_{brand}_{key_suffix}_plan_select"
    active_key = f"disp_{brand}_{key_suffix}_active_plan_id"  # 안전망
    pending = st.session_state.pop(f"rg_{brand}_pending_{key_suffix}_pick", None)
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
        return None
    selected = plans[sel]
    st.session_state[active_key] = selected.id
    return selected


@dataclass
class DispatchData:
    plan: InboundPlan
    items: list[InboundPlanItem]
    sec_items: list[SecondaryItem]
    pa: PalletAssignment
    fc: str
    arr: Any                        # date | None
    yymmdd: str
    yyyymm: str
    datesuf: str
    order_base: str
    ship_prefix: str                # '밀크런' | '택배'
    ship_label: str
    is_milkrun: bool
    label_bytes: bytes | None       # 바코드 라벨 PDF
    attach_bytes: bytes | None      # 부착문서 PDF
    invoice_bytes: bytes | None     # 동봉문서 PDF
    attachment: Any | None          # AttachmentMeta
    invoice: Any | None             # InvoiceMeta or None
    brand_company: str
    plan_files: dict[str, tuple[str, bytes]] = field(default_factory=dict)


def build_dispatch_data(brand: str, brand_company: str, plan: InboundPlan) -> DispatchData | None:
    """탭 3/4 가 공통으로 사용할 dispatch 데이터 빌드.

    필수: plan.status in (verified, completed) + label_pdf/attach_pdf 가 PlanFile 에 존재.
    """
    cfg = load_config()

    with get_session() as s:
        items = s.execute(
            select(InboundPlanItem).where(
                InboundPlanItem.plan_id == plan.id,
                InboundPlanItem.inbound_qty_final > 0,
            )
        ).scalars().all()
        cp_masters = s.execute(
            select(CoupangProduct).where(CoupangProduct.company_name == brand_company)
        ).scalars().all()
        wms_masters = s.execute(
            select(WmsProduct).where(WmsProduct.company_name == brand_company)
        ).scalars().all()

    cp_master_by_opt = {m.coupang_option_id: m for m in cp_masters}
    wms_master_by_bc = {m.wms_barcode: m for m in wms_masters}
    wms_master_by_opt = {m.coupang_option_id: m for m in wms_masters if m.coupang_option_id}

    if not items:
        st.warning("이 plan 에 확정 수량(>0) SKU 가 없습니다.")
        return None

    plan_files = load_plan_files(plan.id)
    label_bytes = plan_files.get("label_pdf", (None, None))[1]
    attach_bytes = plan_files.get("attach_pdf", (None, None))[1]
    invoice_bytes = plan_files.get("invoice_pdf", (None, None))[1]

    if not label_bytes or not attach_bytes:
        st.warning(
            "⚠️ 부착문서 PDF 또는 라벨 PDF 가 누락되어 있습니다. "
            "탭 2 검수 단계에서 PDF 업로드 + 발주 확정 먼저 진행하세요."
        )
        return None

    # 운송방식별 부착문서 파서 분기
    is_parcel_ship = (plan.shipment_type or 'milkrun') == 'parcel'
    if is_parcel_ship:
        attachment = parse_parcel_attachment_doc(attach_bytes)
    else:
        attachment = parse_attachment_doc(attach_bytes)
    invoice = parse_invoice_doc(invoice_bytes) if invoice_bytes else None

    # SecondaryItem 빌드
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
        wmsn = (
            (wp.product_name if wp and wp.product_name else None)
            or (pwp.product_name if pwp and pwp.product_name else None)
        )
        bq = it.box_qty or 1
        _qty = it.inbound_qty_final or 0
        boxes = math.ceil(_qty / max(bq, 1)) if _qty > 0 else 0
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
            _q = it.inbound_qty_final or 0
            boxes_it = math.ceil(_q / max(it.box_qty or 1, 1)) if _q > 0 else 0
            if boxes_it <= 0:
                continue
            pallet_map.setdefault(pn, []).append(
                PalletEntry(key=it.coupang_option_id, name=it.product_name or "", boxes=boxes_it)
            )
        # pallet_count: plan.total_pallets 우선 (저장 시 정확한 값) — 없으면 max(pallet_no)
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
        def _boxes_of(it):
            _q = it.inbound_qty_final or 0
            return math.ceil(_q / max(it.box_qty or 1, 1)) if _q > 0 else 0
        pa_items = [
            PA_PalletItem(
                key=it.coupang_option_id,
                name=it.product_name or "",
                boxes=_boxes_of(it),
            )
            for it in items
            if _boxes_of(it) > 0
        ]
        pa = pa_assign_pallets(pa_items, pallet_size=cfg.pallet_size_boxes)

    fc = plan.fc_name or "동탄1"
    arr = plan.arrival_date or plan.plan_date or _date.today()
    yymmdd = arr.strftime("%y%m%d") if arr else _date.today().strftime("%y%m%d")
    yyyymm = arr.strftime("%Y_%m월") if arr else _date.today().strftime("%Y_%m월")
    datesuf = arr.strftime("%Y%m%d") if arr else _date.today().strftime("%Y%m%d")
    order_base = (
        (invoice.order_id if invoice and invoice.order_id else None)
        or (plan.milkrun_id or attachment.milkrun_id or "")
    )
    is_milkrun = (plan.shipment_type or 'milkrun') == 'milkrun'
    ship_prefix = "밀크런" if is_milkrun else "택배"
    ship_label = SHIPMENT_LABELS.get(plan.shipment_type or 'milkrun', plan.shipment_type or '')

    return DispatchData(
        plan=plan, items=items, sec_items=sec_items, pa=pa,
        fc=fc, arr=arr, yymmdd=yymmdd, yyyymm=yyyymm, datesuf=datesuf,
        order_base=order_base, ship_prefix=ship_prefix, ship_label=ship_label,
        is_milkrun=is_milkrun,
        label_bytes=label_bytes, attach_bytes=attach_bytes, invoice_bytes=invoice_bytes,
        attachment=attachment, invoice=invoice,
        brand_company=brand_company,
        plan_files=plan_files,
    )


def render_context_bar(plan: InboundPlan) -> None:
    """공유 컨텍스트 바 (탭 3/4 상단)."""
    fc = plan.fc_name or "미정"
    arr = plan.arrival_date or "미정"
    milkrun = plan.milkrun_id or "미정"
    sub = derive_substatus_label(plan)
    parts = [
        f'<span style="background:#fef3c7; color:#92400e; padding:3px 8px; '
        f'border-radius:4px; font-weight:700;">#{plan.id}</span>',
        f'<span>{sub}</span>',
        f'<span><b>업체</b> {plan.company_name}</span>',
        f'<span><b>FC</b> {fc}</span>',
        f'<span><b>입고일</b> {arr}</span>',
        f'<span><b>milkrun_id</b> {milkrun}</span>',
    ]
    st.markdown(
        '<div style="display:flex; flex-wrap:wrap; gap:12px; align-items:center; '
        'padding:8px 12px; background:#f9fafb; border:1px solid #e5e7eb; '
        'border-radius:6px; margin:0 0 10px 0; font-size:0.92em;">'
        + "".join(parts) + "</div>",
        unsafe_allow_html=True,
    )
