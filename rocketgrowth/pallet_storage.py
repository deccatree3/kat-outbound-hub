"""팔레트 적재 정보 저장/조회 헬퍼.

InboundPlanPalletEntry 를 단일 진실 출처로 사용.
- save_pallet_assignment: pa 의 pallets 를 그대로 테이블에 저장 (재확정 시 기존 행 삭제 후 재삽입)
- load_pallet_assignment: 테이블에서 pa 를 재구성. 비어있으면 InboundPlanItem.pallet_no fallback → 그것도 없으면 재할당

박스합 검증(assert_pa_within_pallet_size)도 같이 둔다.
"""
from __future__ import annotations

import math
from typing import Sequence

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.orm import Session

from rocketgrowth.models import (
    InboundPlan,
    InboundPlanItem,
    InboundPlanPalletEntry,
)
from rocketgrowth.pallet_assign import (
    PalletAssignment,
    PalletEntry,
    PalletItem as PA_PalletItem,
    assign_pallets,
)


def save_pallet_assignment(
    session: Session,
    plan_id: int,
    pa: PalletAssignment,
    box_qty_by_opt: dict[int, int],
) -> None:
    """pa.pallets 의 각 entry 를 inbound_plan_pallet_entry 행으로 저장.

    기존 plan_id 행은 먼저 삭제. qty = boxes * box_qty(없으면 1).
    """
    session.execute(
        sa_delete(InboundPlanPalletEntry)
        .where(InboundPlanPalletEntry.plan_id == plan_id)
    )
    for pi, pal in enumerate(pa.pallets, start=1):
        for seq_idx, en in enumerate(pal, start=1):
            bq = int(box_qty_by_opt.get(en.key, 1) or 1)
            session.add(InboundPlanPalletEntry(
                plan_id=plan_id,
                pallet_no=pi,
                coupang_option_id=en.key,
                boxes=int(en.boxes),
                qty=int(en.boxes) * bq,
                seq=seq_idx,
            ))


def load_pallet_assignment(
    session: Session,
    plan: InboundPlan,
    items: Sequence[InboundPlanItem],
    pallet_size: int,
) -> PalletAssignment:
    """팔레트 적재 정보를 우선순위에 따라 로드.

    1) InboundPlanPalletEntry 에 행이 있으면 그대로 재구성
    2) 없으면 InboundPlanItem.pallet_no 기반 (deprecated, 분할 정보 손실 위험)
    3) 그것도 없으면 박스수 기준 재할당
    """
    name_by_opt = {it.coupang_option_id: it.product_name or "" for it in items}

    entries = session.execute(
        select(InboundPlanPalletEntry)
        .where(InboundPlanPalletEntry.plan_id == plan.id)
        .order_by(
            InboundPlanPalletEntry.pallet_no,
            InboundPlanPalletEntry.seq,
        )
    ).scalars().all()

    def _boxes_of(it: InboundPlanItem) -> int:
        q = it.inbound_qty_final or 0
        return math.ceil(q / max(int(it.box_qty or 1), 1)) if q > 0 else 0

    def _reassign() -> PalletAssignment:
        pa_items = [
            PA_PalletItem(
                key=it.coupang_option_id,
                name=it.product_name or "",
                boxes=_boxes_of(it),
            )
            for it in items
            if _boxes_of(it) > 0
        ]
        return assign_pallets(pa_items, pallet_size=pallet_size)

    pa: PalletAssignment | None = None

    if entries:
        pallet_map: dict[int, list[PalletEntry]] = {}
        for e in entries:
            pallet_map.setdefault(e.pallet_no, []).append(
                PalletEntry(
                    key=e.coupang_option_id,
                    name=name_by_opt.get(e.coupang_option_id, ""),
                    boxes=int(e.boxes),
                )
            )
        _pallet_count = (
            int(plan.total_pallets) if plan.total_pallets
            else (max(pallet_map.keys()) if pallet_map else 0)
        )
        pa = PalletAssignment(
            pallets=[pallet_map[k] for k in sorted(pallet_map.keys())],
            total_boxes=sum(e.boxes for p in pallet_map.values() for e in p),
            pallet_count=_pallet_count,
        )
    elif any(it.pallet_no for it in items):
        pallet_map = {}
        for it in items:
            boxes_it = _boxes_of(it)
            if boxes_it <= 0:
                continue
            pn = it.pallet_no or 1
            pallet_map.setdefault(pn, []).append(
                PalletEntry(
                    key=it.coupang_option_id,
                    name=it.product_name or "",
                    boxes=boxes_it,
                )
            )
        _pallet_count = (
            int(plan.total_pallets) if plan.total_pallets
            else (max(pallet_map.keys()) if pallet_map else 0)
        )
        pa = PalletAssignment(
            pallets=[pallet_map[k] for k in sorted(pallet_map.keys())],
            total_boxes=sum(e.boxes for p in pallet_map.values() for e in p),
            pallet_count=_pallet_count,
        )

    if pa is None:
        return _reassign()

    # 저장값 검증 — 위반 시 박스수 기준 재할당으로 자동 복구.
    # (legacy pallet_no 단일컬럼이 분할 SKU 를 한 팔레트에 묶은 케이스 보호.)
    try:
        assert_pa_within_pallet_size(pa, pallet_size)
    except ValueError:
        return _reassign()
    return pa


def assert_pa_within_pallet_size(pa: PalletAssignment, pallet_size: int) -> None:
    """팔레트별 박스합이 pallet_size 이하인지 검증. 위반 시 ValueError.

    출력물 다운로드 직전에 호출하여 잘못된 적재 파일이 나가는 것을 차단한다.
    """
    for idx, pal in enumerate(pa.pallets, start=1):
        total = sum(e.boxes for e in pal)
        if total > pallet_size:
            keys = ", ".join(f"{e.key}({e.boxes})" for e in pal)
            raise ValueError(
                f"팔레트 {idx} 박스합({total}) > pallet_size({pallet_size}). "
                f"적재: {keys}"
            )
