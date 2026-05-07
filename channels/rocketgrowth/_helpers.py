"""로켓그로스 공통 helpers — 자매 프로젝트 페이지에서 이전.

원본: nn-rocketgrowth_inventory/app/pages/2_입고_발주_관리.py (lines 71-396).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy import and_, select

from rocketgrowth.db import get_session
from rocketgrowth.ingestion.base import CoupangSnapshot, WmsSnapshot
from rocketgrowth.models import (
    CoupangInventoryItem,
    CoupangInventorySnapshot,
    CoupangProduct,
    InboundPlan,
    InboundPlanItem,
    PlanFile,
    WmsInventoryItem,
    WmsInventorySnapshot,
    WmsProduct,
)


def ni(v):
    """None-safe int 변환."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def resolve_parent_barcode(
    cp_master: Optional[CoupangProduct],
    wms_masters_by_bc: dict[str, WmsProduct],
    wms_masters_by_opt: Optional[dict[int, WmsProduct]] = None,
) -> tuple[Optional[str], int]:
    """coupang 옵션 → (부모 WMS 바코드, unit_qty) 결정.

    - wms_product 의 parent_wms_barcode + unit_qty 우선
    - parent 가 0/None/self 면 '자기 자신이 부모' (단일팩)
    - 다음 케이스에서 옵션ID 역조회 fallback (예: 캐처스 번들 — WMS 단품만):
        1) cp.wms_barcode 가 비어있음
        2) cp.wms_barcode 채워져 있으나 wms_product 에 그 바코드 없음
    """
    if not cp_master:
        return None, 1

    def _try_opt_fallback() -> Optional[tuple[Optional[str], int]]:
        if not wms_masters_by_opt or not cp_master.coupang_option_id:
            return None
        wp = wms_masters_by_opt.get(cp_master.coupang_option_id)
        if not wp:
            return None
        unit_qty = int(wp.unit_qty or 1)
        parent = wp.parent_wms_barcode
        if parent and str(parent) not in ("0", "") and parent != wp.wms_barcode:
            return str(parent), unit_qty
        return wp.wms_barcode, unit_qty

    if cp_master.wms_barcode:
        bc = cp_master.wms_barcode
        wp = wms_masters_by_bc.get(bc)
        if wp:
            unit_qty = int(wp.unit_qty or 1)
            parent = wp.parent_wms_barcode
            if not parent or str(parent) in ("0", "") or parent == bc:
                return bc, unit_qty
            return str(parent), unit_qty
        fb = _try_opt_fallback()
        if fb is not None:
            return fb
        return bc, 1

    fb = _try_opt_fallback()
    if fb is not None:
        return fb
    return None, 1


def upsert_coupang_snapshot(session, snap: CoupangSnapshot) -> CoupangInventorySnapshot:
    """동일 (snapshot_date, source_type) 있으면 그대로 반환, 없으면 신규 + items 일괄 add."""
    existing = session.execute(
        select(CoupangInventorySnapshot).where(
            and_(
                CoupangInventorySnapshot.snapshot_date == snap.snapshot_date,
                CoupangInventorySnapshot.source_type == snap.source_type,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    row = CoupangInventorySnapshot(
        snapshot_date=snap.snapshot_date,
        source_type=snap.source_type,
        source_file=snap.source_file,
    )
    session.add(row)
    session.flush()
    for r in snap.rows:
        session.add(
            CoupangInventoryItem(
                snapshot_id=row.id,
                coupang_option_id=r.coupang_option_id,
                coupang_product_id=r.coupang_product_id,
                sku_id=r.sku_id,
                product_name=r.product_name,
                option_name=r.option_name,
                sales_qty_7d=r.sales_qty_7d,
                sales_qty_30d=r.sales_qty_30d,
                orderable_stock=r.orderable_stock,
                inbound_stock=r.inbound_stock,
                storage_fee_month=r.storage_fee_month,
                expiry_1_30=r.expiry_1_30,
                expiry_31_45=r.expiry_31_45,
                expiry_46_60=r.expiry_46_60,
                expiry_61_120=r.expiry_61_120,
                expiry_121_180=r.expiry_121_180,
                expiry_181_plus=r.expiry_181_plus,
                recommendation=r.recommendation,
                raw=r.raw,
            )
        )
    return row


def upsert_wms_snapshot(session, snap: WmsSnapshot) -> WmsInventorySnapshot:
    existing = session.execute(
        select(WmsInventorySnapshot).where(
            WmsInventorySnapshot.snapshot_date == snap.snapshot_date
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    row = WmsInventorySnapshot(snapshot_date=snap.snapshot_date, source_file=snap.source_file)
    session.add(row)
    session.flush()
    for r in snap.rows:
        session.add(
            WmsInventoryItem(
                snapshot_id=row.id,
                barcode=r.barcode,
                product_name=r.product_name,
                loc_group=r.loc_group,
                loc=r.loc,
                total_qty=r.total_qty,
                alloc_qty=r.alloc_qty,
                available_qty=r.available_qty,
                expiry_short=r.expiry_short,
                expiry_long=r.expiry_long,
                raw=r.raw,
            )
        )
    return row


def save_plan(
    cp_snap: CoupangSnapshot,
    wms_snap: WmsSnapshot,
    full_df: pd.DataFrame,
    company_name: str = "서현",
    shipment_type: str = "milkrun",
    total_weight_kg: Optional[float] = None,
    movement_blob: Optional[bytes] = None,
    movement_filename: Optional[str] = None,
    raw_files: Optional[dict[str, tuple[str, bytes]]] = None,
) -> int:
    """draft 상태로 InboundPlan 저장. 작업일/FC/작업자/검수메타는 검수 단계에서 채움."""
    with get_session() as session:
        cp_row = upsert_coupang_snapshot(session, cp_snap)
        wms_row = upsert_wms_snapshot(session, wms_snap)
        session.flush()

        plan = InboundPlan(
            company_name=company_name,
            shipment_type=shipment_type,
            plan_date=date.today(),
            fc_name=None,
            worker=None,
            coupang_snapshot_id=cp_row.id,
            wms_snapshot_id=wms_row.id,
            status="draft",
            total_weight_kg=total_weight_kg,
            movement_template_blob=movement_blob,
            movement_template_filename=movement_filename,
        )
        session.add(plan)
        session.flush()

        for _, row in full_df.iterrows():
            final_qty = int(row["inbound_final"] or 0)
            box_qty = int(row["box_qty"] or 1)
            session.add(
                InboundPlanItem(
                    plan_id=plan.id,
                    coupang_option_id=int(row["coupang_option_id"]),
                    product_name=row["product_name"],
                    option_name=row.get("option_name"),
                    current_total_stock=int(
                        (row["orderable"] or 0) + (row["inbound_stock"] or 0)
                    ),
                    sales_7d=int(row["sales_7d"] or 0),
                    sales_30d=int(row["sales_30d"] or 0),
                    sales_velocity_daily=float(row["velocity"] or 0),
                    stock_after_1w=None,
                    stock_after_2w=None,
                    stock_after_4w=float(row["stock_4w"] or 0),
                    box_qty=box_qty,
                    inbound_qty_suggested=int(row.get("inbound_basic") or 0),
                    inbound_qty_final=final_qty,
                    inbound_boxes=final_qty // max(box_qty, 1),
                    days_sellable_after=(
                        float(row["days_sellable_after"])
                        if row["days_sellable_after"] is not None else None
                    ),
                    wms_short_expiry=row.get("selected_batch_expiry"),
                    wms_long_expiry=None,
                )
            )
        if raw_files:
            for ftype, (fname, fbytes) in raw_files.items():
                session.merge(PlanFile(
                    plan_id=plan.id, file_type=ftype,
                    file_name=fname, content=fbytes,
                ))
        session.commit()
        return plan.id


def save_plan_files(plan_id: int, files: dict[str, tuple[str, bytes]]):
    """기존 plan 에 파일 추가/갱신."""
    with get_session() as session:
        for ftype, (fname, fbytes) in files.items():
            existing = session.execute(
                select(PlanFile).where(
                    PlanFile.plan_id == plan_id, PlanFile.file_type == ftype
                )
            ).scalar_one_or_none()
            if existing:
                existing.file_name = fname
                existing.content = fbytes
            else:
                session.add(PlanFile(
                    plan_id=plan_id, file_type=ftype,
                    file_name=fname, content=fbytes,
                ))
        session.commit()


def load_plan_files(plan_id: int) -> dict[str, tuple[str, bytes]]:
    """plan_id 의 PlanFile 들 → {file_type: (file_name, content)}"""
    with get_session() as session:
        rows = session.execute(
            select(PlanFile).where(PlanFile.plan_id == plan_id)
        ).scalars().all()
        return {r.file_type: (r.file_name, bytes(r.content)) for r in rows}


STATUS_LABELS = {"draft": "📝 임시저장", "verified": "✅ 발주확정", "completed": "🏁 완료"}


def section_note(text: str) -> None:
    """섹션 헤더 아래 안내 — 좌측 파란 테두리 + 옅은 파란 배경."""
    import streamlit as st
    st.markdown(
        f'<div style="border-left:4px solid #3b82f6; padding:10px 14px; '
        f'margin:4px 0 16px 0; background:#eff6ff; color:#1e3a8a; '
        f'font-size:0.95em; line-height:1.55;">{text}</div>',
        unsafe_allow_html=True,
    )
