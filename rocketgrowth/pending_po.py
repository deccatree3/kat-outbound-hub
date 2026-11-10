"""재고 모니터링(nn-stock-forecast) 발주 DB 의 입고대기 물량 조회 (read-only, cross-project).

자매 프로젝트 nn-stock-forecast 가 같은 Supabase 에 적재한 발주/입고 테이블에서
상품별 미입고 잔량(= 발주수량 − 입고수량 합, 양수만)을 WMS 바코드 기준으로 집계한다.
재고 모니터링의 `입고대기` (src/po_db.pending_qty_by_product) 와 동일 산식.

원칙:
- nn_* 테이블은 READ-ONLY. write 절대 없음.
- 어떤 예외도 {} 로 흡수 — 테이블 부재/스키마 변경이 입고 계획 화면을 막지 않는다.
- 매칭 키 = nn_products.바코드 (= wms_product.wms_barcode). trim + 대문자 정규화.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from .db import get_session

_SQL = text(
    """
    SELECT upper(btrim(p.바코드)) AS barcode,
           SUM(po.발주수량 - COALESCE(r.s, 0)) AS pending
    FROM nn_purchase_orders po
    JOIN nn_products p ON p.상품코드 = po.상품코드
    LEFT JOIN (
        SELECT 발주_id, SUM(입고수량) AS s FROM nn_receipts GROUP BY 발주_id
    ) r ON r.발주_id = po.id
    WHERE po.상품코드 IS NOT NULL
      AND p.바코드 IS NOT NULL
      AND btrim(p.바코드) <> ''
      AND (po.발주수량 - COALESCE(r.s, 0)) > 0
    GROUP BY upper(btrim(p.바코드))
    """
)


def pending_po_by_barcode() -> dict[str, int]:
    """WMS 바코드(대문자·trim) → 입고대기 수량. 조회 실패 시 {}."""
    try:
        with get_session() as s:
            rows = s.execute(_SQL).all()
        return {str(bc): int(qty or 0) for bc, qty in rows if bc and qty}
    except Exception as e:  # noqa: BLE001 — 조회 실패해도 입고 계획은 계속
        logging.getLogger(__name__).warning("입고대기(발주 DB) 조회 실패 (무시): %r", e)
        return {}
