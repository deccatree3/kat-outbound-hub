"""
채널 상품 매핑 (channel_product_mapping 테이블) — 채널 공유 CRUD.

스키마:
  channel        TEXT NOT NULL
  product_name   TEXT NOT NULL
  product_option TEXT NOT NULL
  item_codes     TEXT NOT NULL  -- 콤마 구분 (item 1: name1, item 2: name2, ...)
  sku_codes      TEXT NOT NULL  -- 콤마 구분
  quantities     TEXT NOT NULL  -- 콤마 구분 정수
  note           TEXT
  updated_at     TIMESTAMP
  PK (channel, product_name, product_option)

용도:
  - Qoo10 일본 (channel='qoo10_japan')
  - 캐처스 큐텐 국내 KSE (channel='cachers_qoo10_kr')
  - 향후 채널 추가 시 channel 값만 바꿔서 재사용
"""
from typing import Dict, List, Optional, Tuple

from db import pg


SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS channel_product_mapping (
    channel        TEXT NOT NULL,
    product_name   TEXT NOT NULL,
    product_option TEXT NOT NULL,
    item_codes     TEXT NOT NULL,
    sku_codes      TEXT NOT NULL,
    quantities     TEXT NOT NULL,
    note           TEXT,
    updated_at     TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul'),
    PRIMARY KEY (channel, product_name, product_option)
);
CREATE INDEX IF NOT EXISTS idx_cpm_channel ON channel_product_mapping (channel);
"""


_ENSURED = False


def ensure_schema() -> bool:
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


def upsert(channel: str, product_name: str, product_option: str,
           skus: List[Tuple[str, str, int]], note: Optional[str] = None) -> bool:
    """매핑 upsert. skus = [(sku_code, sku_name, qty), ...]"""
    ensure_schema()
    if not channel or not product_name:
        return False
    item_codes = ','.join(s[1] for s in skus)
    sku_codes = ','.join(s[0] for s in skus)
    quantities = ','.join(str(s[2]) for s in skus)
    try:
        conn = pg.connect()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO channel_product_mapping
                (channel, product_name, product_option, item_codes, sku_codes, quantities, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (channel, product_name, product_option) DO UPDATE SET
                    item_codes = EXCLUDED.item_codes,
                    sku_codes  = EXCLUDED.sku_codes,
                    quantities = EXCLUDED.quantities,
                    note       = COALESCE(EXCLUDED.note, channel_product_mapping.note),
                    updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')
            """, (channel, product_name, product_option or '',
                  item_codes, sku_codes, quantities, note))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def delete(channel: str, product_name: str, product_option: str) -> bool:
    ensure_schema()
    try:
        conn = pg.connect()
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM channel_product_mapping
                WHERE channel=%s AND product_name=%s AND product_option=%s
            """, (channel, product_name, product_option or ''))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def load_for_channel(channel: str) -> Dict[Tuple[str, str], Dict]:
    """채널별 매핑 dict 반환. key=(product_name, product_option)"""
    ensure_schema()
    try:
        conn = pg.connect(autocommit=True)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT product_name, product_option, item_codes, sku_codes, quantities, note
                FROM channel_product_mapping
                WHERE channel = %s
            """, (channel,))
            rows = cur.fetchall()
        conn.close()
    except Exception:
        return {}

    result = {}
    for r in rows:
        result[(r[0], r[1] or '')] = {
            'item_codes': (r[2] or '').split(','),
            'sku_codes':  (r[3] or '').split(','),
            'quantities': [int(x) for x in (r[4] or '1').split(',') if x.strip()],
            'note':       r[5],
            'enabled':    True,  # 채널로 분리되어 enabled 의미 없음 (호환용)
        }
    return result


def list_known_skus() -> List[Dict]:
    """모든 매핑의 sku_codes/item_codes 에서 distinct (sku_code, sku_name) 추출.
    매핑 테이블이 SKU 마스터 역할을 겸함 (별도 sku_catalog 폐기).

    같은 sku_code가 다른 매핑에서 다른 sku_name 으로 나오면 가장 최근 갱신 매핑의 이름 사용.
    """
    ensure_schema()
    try:
        conn = pg.connect(autocommit=True)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT sku_codes, item_codes, updated_at
                FROM channel_product_mapping
                ORDER BY updated_at DESC NULLS LAST
            """)
            rows = cur.fetchall()
        conn.close()
    except Exception:
        return []

    seen: Dict[str, str] = {}
    for sku_codes, item_codes, _ in rows:
        codes = [c.strip() for c in (sku_codes or '').split(',')]
        names = [n.strip() for n in (item_codes or '').split(',')]
        for i, code in enumerate(codes):
            if not code or code == '-':
                continue
            if code in seen:
                continue
            name = names[i] if i < len(names) else ''
            seen[code] = name

    return [{'sku_code': c, 'sku_name': n}
            for c, n in sorted(seen.items(), key=lambda x: (x[1] or '', x[0]))]


def list_all(channel: Optional[str] = None,
             search: Optional[str] = None) -> List[Dict]:
    """모든 매핑 행을 반환 (관리 페이지용). channel 필터 / 검색 지원."""
    ensure_schema()
    conds = []
    params: List = []
    if channel:
        conds.append("channel = %s")
        params.append(channel)
    if search:
        conds.append("(product_name ILIKE %s OR product_option ILIKE %s "
                     "OR sku_codes ILIKE %s OR item_codes ILIKE %s)")
        s = f"%{search}%"
        params.extend([s, s, s, s])
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    sql = f"""
        SELECT channel, product_name, product_option,
               item_codes, sku_codes, quantities, note, updated_at
        FROM channel_product_mapping
        {where}
        ORDER BY channel, product_name, product_option
    """
    try:
        conn = pg.connect(autocommit=True)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.close()
    except Exception:
        return []

    return [{'channel': r[0], 'product_name': r[1], 'product_option': r[2] or '',
             'item_codes': r[3] or '', 'sku_codes': r[4] or '',
             'quantities': r[5] or '', 'note': r[6], 'updated_at': r[7]}
            for r in rows]


def count_by_channel() -> Dict[str, int]:
    ensure_schema()
    try:
        conn = pg.connect(autocommit=True)
        with conn.cursor() as cur:
            cur.execute("SELECT channel, COUNT(*) FROM channel_product_mapping GROUP BY channel")
            rows = cur.fetchall()
        conn.close()
        return {r[0]: int(r[1]) for r in rows}
    except Exception:
        return {}
