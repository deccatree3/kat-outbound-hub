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
    existing_plan_id: Optional[int] = None,
) -> int:
    """수량확정 상태로 InboundPlan 저장 또는 갱신.

    existing_plan_id 가 주어지면 해당 plan 을 update (items 전체 재생성, raw_files merge).
    검수 메타(FC/입고일/작업자/milkrun_id) 는 보존.
    """
    from sqlalchemy import delete  # local import (모듈 상단 의존성 노출 회피)
    with get_session() as session:
        cp_row = upsert_coupang_snapshot(session, cp_snap)
        wms_row = upsert_wms_snapshot(session, wms_snap)
        session.flush()

        if existing_plan_id is not None:
            plan = session.get(InboundPlan, existing_plan_id)
            if plan is None:
                raise ValueError(f"plan #{existing_plan_id} not found")
            plan.coupang_snapshot_id = cp_row.id
            plan.wms_snapshot_id = wms_row.id
            plan.status = "qty_confirmed"
            plan.total_weight_kg = total_weight_kg
            plan.shipment_type = shipment_type
            if movement_blob:
                plan.movement_template_blob = movement_blob
                plan.movement_template_filename = movement_filename
            # 기존 items 삭제 후 신규 items 재생성
            session.execute(
                delete(InboundPlanItem).where(InboundPlanItem.plan_id == plan.id)
            )
            session.flush()
        else:
            plan = InboundPlan(
                company_name=company_name,
                shipment_type=shipment_type,
                plan_date=date.today(),
                fc_name=None,
                worker=None,
                coupang_snapshot_id=cp_row.id,
                wms_snapshot_id=wms_row.id,
                status="qty_confirmed",
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
            # (plan_id, file_type) unique constraint 회피 — merge 는 PK 만 보고 dedup
            for ftype, (fname, fbytes) in raw_files.items():
                existing_pf = session.execute(
                    select(PlanFile).where(
                        PlanFile.plan_id == plan.id,
                        PlanFile.file_type == ftype,
                    )
                ).scalar_one_or_none()
                if existing_pf:
                    existing_pf.file_name = fname
                    existing_pf.content = fbytes
                else:
                    session.add(PlanFile(
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


STATUS_LABELS = {
    "draft": "📝 임시저장",
    "qty_confirmed": "📋 수량확정",
    "inbound_confirmed": "📦 입고확정",
    "verified": "🚚 출고요청 확정",
    "completed": "🏁 완료",
}


def get_fc_info(fc_name: str):
    """쿠팡 FC 정보 조회. 없으면 None."""
    from rocketgrowth.models import CoupangFcInfo
    with get_session() as s:
        return s.execute(
            select(CoupangFcInfo).where(CoupangFcInfo.fc_name == fc_name)
        ).scalar_one_or_none()


def upsert_fc_info(
    fc_name: str, address: str, postal_code: str, phone: str,
    fc_code: str | None = None, note: str | None = None,
) -> None:
    """쿠팡 FC 정보 upsert."""
    from rocketgrowth.models import CoupangFcInfo
    with get_session() as s:
        existing = s.execute(
            select(CoupangFcInfo).where(CoupangFcInfo.fc_name == fc_name)
        ).scalar_one_or_none()
        if existing:
            existing.address = address
            existing.postal_code = postal_code
            existing.phone = phone
            if fc_code is not None:
                existing.fc_code = fc_code
            if note is not None:
                existing.note = note
        else:
            s.add(CoupangFcInfo(
                fc_name=fc_name, address=address, postal_code=postal_code,
                phone=phone, fc_code=fc_code, note=note,
            ))
        s.commit()

# 입고확정 이상이면 수량 잠금 (탭 1 수량확정 버튼 disabled)
QTY_LOCKED_STATUSES = {"inbound_confirmed", "verified", "completed"}


def derive_substatus_label(plan, has_attach_pdf: bool = False) -> str:
    """plan 의 status 라벨.

    상태 흐름:
      draft → qty_confirmed → inbound_confirmed → verified → completed

    매핑 (status 단순 매핑, attach 시그널 무시):
    - draft             → 📝 임시저장
    - qty_confirmed     → 📋 수량확정
    - inbound_confirmed → 📦 입고확정
    - verified          → 🚚 출고요청 확정
    - completed         → 🏁 완료

    has_attach_pdf 파라미터는 호환성을 위해 유지 (현재 라벨에 영향 없음).
    """
    s = plan.status or "draft"
    if s == "inbound_confirmed":
        return "📦 입고확정"
    if s == "verified":
        return "🚚 출고요청 확정"
    if s == "completed":
        return "🏁 완료"
    if s == "qty_confirmed":
        return "📋 수량확정"
    return "📝 임시저장"


SHIPMENT_LABELS = {'milkrun': '밀크런', 'parcel': '택배'}


def format_plan_label(plan, has_attach_pdf: bool = False) -> str:
    """발주 계획 dropdown 라벨 (탭 1~4 공통).

    형식: '#[번호] [상태] · [화주사]-[MM/DD 생성일] · 입고-[YYYY-MM-DD] · [FC] · [운송]'
    값 없으면 '—' 표시.
    """
    sub = derive_substatus_label(plan, has_attach_pdf=has_attach_pdf)
    company = plan.company_name or "—"
    create_md = plan.plan_date.strftime("%m/%d") if plan.plan_date else "—"
    arr_str = plan.arrival_date.strftime("%Y-%m-%d") if plan.arrival_date else "—"
    fc_str = plan.fc_name or "—"
    ship_str = SHIPMENT_LABELS.get(plan.shipment_type or "", plan.shipment_type or "—")
    return (
        f"#{plan.id} · {sub} · {company}-{create_md} · "
        f"입고-{arr_str} · {fc_str} · {ship_str}"
    )


def is_agetshot_bundle(cp_master, wms_master) -> bool:
    """에이지샷 번들 SKU 식별 ([캐처스]로켓그로스 박스 계산용).

    조건 (둘 중 하나라도 매칭):
    - 쿠팡 등록상품명에 '에이지샷' + 옵션명에 '2개' 또는 '3개'
    - WMS 제품명에 '에이지샷' + 낱개수량(unit_qty) 2 또는 3
    """
    if cp_master is not None:
        pname = (cp_master.product_name or '')
        oname = (cp_master.option_name or '')
        if '에이지샷' in pname and ('2개' in oname or '3개' in oname):
            return True
    if wms_master is not None:
        wname = (wms_master.product_name or '')
        uq = wms_master.unit_qty
        if '에이지샷' in wname and (uq == 2 or uq == 3):
            return True
    return False


# 에이지샷 박스 capacity (에이지샷 9호 = 대2 = max 100 인박스)
AGETSHOT_BOX_CAPACITY = 100


def jump_to_tab(tab_index: int) -> None:
    """JS injection 으로 Streamlit 탭 자동 전환.

    문제 — 단순 components.html 호출 시:
    - iframe srcdoc 이 동일하면 streamlit/브라우저가 iframe 재로드 X → JS 미실행
    - 페이지 로딩 중 tabs DOM 미준비 시 querySelectorAll 0개

    해결:
    - nonce (time.time_ns) 로 매 호출마다 srcdoc 다르게 → iframe 강제 재렌더
    - setTimeout 재시도 (tabs 미준비 시 100ms 마다 재시도, 30회 max)
    """
    import time
    import streamlit.components.v1 as components
    nonce = time.time_ns()
    components.html(
        f"""
        <script>
        // nonce={nonce}
        let _tries = 0;
        function _tryClickTab() {{
            _tries += 1;
            try {{
                const tabs = window.parent.document.querySelectorAll('button[role="tab"]');
                if (tabs.length > {tab_index}) {{
                    tabs[{tab_index}].click();
                    window.parent.scrollTo({{top: 0, behavior: 'smooth'}});
                    return;
                }}
            }} catch (e) {{ /* parent access blocked */ }}
            if (_tries < 30) setTimeout(_tryClickTab, 100);
        }}
        _tryClickTab();
        </script>
        """,
        height=0,
    )


def section_note(text: str) -> None:
    """섹션 헤더 아래 안내 — 좌측 파란 테두리 + 옅은 파란 배경."""
    import streamlit as st
    st.markdown(
        f'<div style="border-left:4px solid #3b82f6; padding:10px 14px; '
        f'margin:4px 0 16px 0; background:#eff6ff; color:#1e3a8a; '
        f'font-size:0.95em; line-height:1.55;">{text}</div>',
        unsafe_allow_html=True,
    )
