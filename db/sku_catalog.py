"""
SKU 카탈로그 (sku_catalog 테이블).

스키마:
  sku_code   TEXT PRIMARY KEY
  sku_name   TEXT
  notes      TEXT
  updated_at TIMESTAMP

용도:
  - 매핑 등록 시 SKU 드롭다운 source
  - 모든 채널 공유 — 창고 위치(JP/KR) 구분은 channel_product_mapping(channel) 으로 도출

기존 location='JP'/'KR' 분리 컬럼은 삭제됨. 같은 SKU가 두 창고에 모두 존재하는
시나리오가 발생하면 별도 inventory(sku_code, warehouse) 테이블로 분리.
"""
from typing import Dict, List, Optional

from db import pg


SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS sku_catalog (
    sku_code   TEXT PRIMARY KEY,
    sku_name   TEXT,
    notes      TEXT,
    updated_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')
);
CREATE INDEX IF NOT EXISTS idx_sku_catalog_name ON sku_catalog (sku_name);
"""


_ENSURED = False


def ensure_schema() -> bool:
    """테이블 생성. 캐시되어 세션당 1회만 실행."""
    global _ENSURED
    if _ENSURED:
        return True
    try:
        conn = pg.connect()
        with conn.cursor() as cur:
            for stmt in SCHEMA_DDL.split(';'):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)
        conn.commit()
        conn.close()
        _ENSURED = True
        return True
    except Exception:
        return False


def list_skus(search: Optional[str] = None) -> List[Dict]:
    """카탈로그 조회."""
    ensure_schema()
    conds = []
    params = []
    if search:
        conds.append("(sku_code ILIKE %s OR sku_name ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    sql = f"""
        SELECT sku_code, sku_name, notes, updated_at
        FROM sku_catalog
        {where}
        ORDER BY sku_name NULLS LAST, sku_code
    """
    try:
        conn = pg.connect(autocommit=True)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.close()
        return [
            {'sku_code': r[0], 'sku_name': r[1], 'notes': r[2], 'updated_at': r[3]}
            for r in rows
        ]
    except Exception:
        return []


def upsert_sku(sku_code: str, sku_name: str = '', notes: str = '') -> bool:
    """추가/수정 (sku_code PK)."""
    ensure_schema()
    if not sku_code:
        return False
    try:
        conn = pg.connect()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sku_catalog (sku_code, sku_name, notes, updated_at)
                VALUES (%s, %s, %s, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul'))
                ON CONFLICT (sku_code) DO UPDATE SET
                    sku_name = EXCLUDED.sku_name,
                    notes    = EXCLUDED.notes,
                    updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')
            """, (sku_code.strip(), (sku_name or '').strip(), (notes or '').strip()))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def delete_sku(sku_code: str) -> bool:
    ensure_schema()
    try:
        conn = pg.connect()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sku_catalog WHERE sku_code=%s", (sku_code,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def total_count() -> int:
    ensure_schema()
    try:
        conn = pg.connect(autocommit=True)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM sku_catalog")
            n = cur.fetchone()[0]
        conn.close()
        return int(n)
    except Exception:
        return 0
