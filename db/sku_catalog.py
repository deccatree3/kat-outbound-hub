"""
KSE SKU 카탈로그 (kse_sku_catalog 테이블).

스키마:
  sku_code  TEXT NOT NULL
  sku_name  TEXT
  location  TEXT NOT NULL ('JP' or 'KR')   ← 일본 KSE / 한국 KSE(다원) 출고 대상 구분
  enabled   BOOLEAN DEFAULT TRUE
  notes     TEXT
  updated_at TIMESTAMP
  PK (sku_code, location)

용도:
  - 매핑 등록 시 SKU 드롭다운 (location 별)
  - load_kse_sku_catalog(location) 의 source

시드:
  최초 ensure_schema() 시 자매 프로젝트의 stock_snapshots/shipments에서 distinct SKU를
  location='JP' 로 자동 import (이미 행이 있으면 skip).
"""
from typing import Dict, List, Optional

from db import pg


SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS kse_sku_catalog (
    sku_code   TEXT NOT NULL,
    location   TEXT NOT NULL CHECK (location IN ('JP', 'KR')),
    sku_name   TEXT,
    enabled    BOOLEAN DEFAULT TRUE,
    notes      TEXT,
    updated_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul'),
    PRIMARY KEY (sku_code, location)
);
CREATE INDEX IF NOT EXISTS idx_kse_sku_catalog_name ON kse_sku_catalog (sku_name);
"""

# 시드 SQL: 자매 프로젝트의 stock_snapshots + shipments → kse_sku_catalog (location='JP')
# 두 테이블이 없으면 조용히 실패 (try/except 처리)
SEED_FROM_LEGACY_SQL = """
INSERT INTO kse_sku_catalog (sku_code, sku_name, location)
SELECT DISTINCT sku_code, sku_name, 'JP'
FROM (
    SELECT sku_code, sku_name FROM stock_snapshots
    UNION
    SELECT sku_code, sku_name FROM shipments
) t
WHERE sku_code IS NOT NULL AND sku_code != ''
  AND sku_name IS NOT NULL AND sku_name != ''
ON CONFLICT (sku_code, location) DO NOTHING;
"""


_ENSURED = False


def ensure_schema() -> bool:
    """테이블 생성 + (1회) 시드. 캐시되어 세션당 1회만 실행."""
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
        # 시드: 비어 있는 경우에만 시도 (자매 프로젝트 테이블 없으면 silently skip)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM kse_sku_catalog WHERE location='JP'")
            n = cur.fetchone()[0]
        if n == 0:
            try:
                with conn.cursor() as cur:
                    cur.execute(SEED_FROM_LEGACY_SQL)
                conn.commit()
            except Exception:
                conn.rollback()
        conn.close()
        _ENSURED = True
        return True
    except Exception:
        return False


def list_skus(location: Optional[str] = None,
              enabled_only: bool = False,
              search: Optional[str] = None) -> List[Dict]:
    """카탈로그 조회. location 미지정시 전체."""
    ensure_schema()
    conds = []
    params = []
    if location:
        conds.append("location = %s")
        params.append(location)
    if enabled_only:
        conds.append("enabled = TRUE")
    if search:
        conds.append("(sku_code ILIKE %s OR sku_name ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    sql = f"""
        SELECT sku_code, location, sku_name, enabled, notes, updated_at
        FROM kse_sku_catalog
        {where}
        ORDER BY location, sku_name NULLS LAST, sku_code
    """
    try:
        conn = pg.connect(autocommit=True)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.close()
        return [
            {'sku_code': r[0], 'location': r[1], 'sku_name': r[2],
             'enabled': bool(r[3]), 'notes': r[4], 'updated_at': r[5]}
            for r in rows
        ]
    except Exception:
        return []


def upsert_sku(sku_code: str, location: str, sku_name: str = '',
               enabled: bool = True, notes: str = '') -> bool:
    """추가/수정 (sku_code, location) PK 기준."""
    ensure_schema()
    if not sku_code or location not in ('JP', 'KR'):
        return False
    try:
        conn = pg.connect()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO kse_sku_catalog (sku_code, location, sku_name, enabled, notes, updated_at)
                VALUES (%s, %s, %s, %s, %s, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul'))
                ON CONFLICT (sku_code, location) DO UPDATE SET
                    sku_name = EXCLUDED.sku_name,
                    enabled  = EXCLUDED.enabled,
                    notes    = EXCLUDED.notes,
                    updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')
            """, (sku_code.strip(), location, (sku_name or '').strip(),
                  bool(enabled), (notes or '').strip()))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def delete_sku(sku_code: str, location: str) -> bool:
    ensure_schema()
    try:
        conn = pg.connect()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM kse_sku_catalog WHERE sku_code=%s AND location=%s",
                (sku_code, location),
            )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def count_by_location() -> Dict[str, int]:
    ensure_schema()
    try:
        conn = pg.connect(autocommit=True)
        with conn.cursor() as cur:
            cur.execute("SELECT location, COUNT(*) FROM kse_sku_catalog GROUP BY location")
            rows = cur.fetchall()
        conn.close()
        return {r[0]: int(r[1]) for r in rows}
    except Exception:
        return {}
