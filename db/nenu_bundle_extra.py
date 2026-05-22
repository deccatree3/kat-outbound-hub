"""네뉴 번들 마스터 템플릿 오버레이 (nenu_bundle_extra 테이블).

`outputs/nenu_bundle/template.xlsx` 는 git 레포 파일이라 Streamlit Cloud 런타임에
직접 수정하면 재배포 시 사라진다. 그래서 마스터에 없는 신규 선물세트 SKU 는
이 DB 테이블에 저장하고, 번들파일 생성(build_bundle_xlsx) 시 세트 행으로 병합한다.

스키마:
  barcode      TEXT PK   -- 세트 상품 바코드 (예: 8809744303840)
  product_name TEXT      -- 세트 상품명 (예: ...류신 타블렛 단백질(60정) 선물세트(3개입))
  set_units    INTEGER   -- 세트 개입수 (E열, 예: 3)
  parent_name  TEXT      -- 모체 단품명 (G열, SUMIFS 집계 연결용; 템플릿 단품명과 일치해야 함)
  note         TEXT
  updated_at   TIMESTAMP
"""
from typing import Dict, List, Optional

from db import pg


SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS nenu_bundle_extra (
    barcode      TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    set_units    INTEGER NOT NULL DEFAULT 1,
    parent_name  TEXT,
    note         TEXT,
    updated_at   TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')
);
"""

_ENSURED = False


def ensure_schema() -> bool:
    global _ENSURED
    if _ENSURED:
        return True
    try:
        conn = pg.connect()
        with conn.cursor() as cur:
            cur.execute(SCHEMA_DDL)
        conn.commit()
        conn.close()
        _ENSURED = True
        return True
    except Exception:
        return False


def upsert(barcode: str, product_name: str, set_units: int,
           parent_name: Optional[str] = None, note: Optional[str] = None) -> bool:
    """세트 SKU 오버레이 upsert. barcode 필수."""
    ensure_schema()
    bc = str(barcode or '').strip()
    if not bc or not product_name:
        return False
    try:
        conn = pg.connect()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO nenu_bundle_extra
                    (barcode, product_name, set_units, parent_name, note)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (barcode) DO UPDATE SET
                    product_name = EXCLUDED.product_name,
                    set_units    = EXCLUDED.set_units,
                    parent_name  = EXCLUDED.parent_name,
                    note         = COALESCE(EXCLUDED.note, nenu_bundle_extra.note),
                    updated_at   = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')
            """, (bc, str(product_name).strip(), int(set_units or 1),
                  (parent_name or '').strip() or None, note))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def load_all() -> List[Dict]:
    """모든 오버레이 세트 SKU. [{barcode, product_name, set_units, parent_name}]."""
    ensure_schema()
    try:
        conn = pg.connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT barcode, product_name, set_units, parent_name "
                "FROM nenu_bundle_extra ORDER BY barcode"
            )
            rows = cur.fetchall()
        conn.close()
        return [
            {'barcode': r[0], 'product_name': r[1],
             'set_units': r[2] or 1, 'parent_name': r[3]}
            for r in rows
        ]
    except Exception:
        return []


def delete(barcode: str) -> bool:
    ensure_schema()
    try:
        conn = pg.connect()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM nenu_bundle_extra WHERE barcode = %s",
                        (str(barcode or '').strip(),))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False
