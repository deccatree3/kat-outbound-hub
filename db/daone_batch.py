"""
다원 발주서 통합용 작업 내역 (daone_pending_batch).

각 batch = 한 채널의 (작업일, 차수) 단위 다원 19컬럼 행 묶음.
사용자가 "💾 저장" 클릭 시 upsert. 통합 페이지에서 여러 채널 batch 선택해서 합쳐 다운로드.

스키마:
  id              SERIAL PK
  work_date       DATE
  sequence        INT
  channel         TEXT
  row_count       INT
  rows_json       JSONB    (다원 19컬럼 dict array)
  source_filename TEXT     (원본 업로드 파일명, multi면 join)
  note            TEXT     (사용자 자유 메모)
  created_at      TIMESTAMP
  updated_at      TIMESTAMP
  UNIQUE (work_date, sequence, channel)  -- 같은 키 = 덮어쓰기
"""
import datetime
import json
from typing import Dict, List, Optional, Tuple

from db import pg


SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS daone_pending_batch (
    id              SERIAL PRIMARY KEY,
    work_date       DATE NOT NULL,
    sequence        INT  NOT NULL,
    channel         TEXT NOT NULL,
    row_count       INT  NOT NULL,
    rows_json       JSONB NOT NULL,
    source_filename TEXT,
    note            TEXT,
    created_at      TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul'),
    updated_at      TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul'),
    UNIQUE (work_date, sequence, channel)
);
CREATE INDEX IF NOT EXISTS idx_dpb_filter
    ON daone_pending_batch (work_date DESC, sequence DESC, channel);
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


def upsert(work_date: datetime.date, sequence: int, channel: str,
           rows: List[Dict], source_filename: Optional[str] = None,
           note: Optional[str] = None) -> bool:
    """동일 (work_date, sequence, channel) 키 덮어쓰기."""
    ensure_schema()
    if not channel or not rows:
        return False
    payload = json.dumps(rows, ensure_ascii=False, default=str)
    try:
        conn = pg.connect()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO daone_pending_batch
                (work_date, sequence, channel, row_count, rows_json, source_filename, note)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (work_date, sequence, channel) DO UPDATE SET
                    row_count       = EXCLUDED.row_count,
                    rows_json       = EXCLUDED.rows_json,
                    source_filename = COALESCE(EXCLUDED.source_filename, daone_pending_batch.source_filename),
                    note            = COALESCE(EXCLUDED.note, daone_pending_batch.note),
                    updated_at      = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')
            """, (work_date, int(sequence), channel, len(rows), payload,
                  source_filename, note))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get(work_date: datetime.date, sequence: int, channel: str) -> Optional[Dict]:
    """단일 batch 조회. 없으면 None."""
    ensure_schema()
    try:
        conn = pg.connect(autocommit=True)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT work_date, sequence, channel, row_count, rows_json,
                       source_filename, note, created_at, updated_at
                FROM daone_pending_batch
                WHERE work_date=%s AND sequence=%s AND channel=%s
            """, (work_date, int(sequence), channel))
            row = cur.fetchone()
        conn.close()
    except Exception:
        return None
    if not row:
        return None
    rows_data = row[4]
    if isinstance(rows_data, str):
        rows_data = json.loads(rows_data)
    return {
        'work_date': row[0], 'sequence': row[1], 'channel': row[2],
        'row_count': row[3], 'rows': rows_data,
        'source_filename': row[5], 'note': row[6],
        'created_at': row[7], 'updated_at': row[8],
    }


def list_for_session(work_date: datetime.date, sequence: int) -> List[Dict]:
    """(work_date, sequence) 의 모든 채널 batch 메타 (rows 제외, 가벼움)."""
    ensure_schema()
    try:
        conn = pg.connect(autocommit=True)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT work_date, sequence, channel, row_count,
                       source_filename, note, created_at, updated_at
                FROM daone_pending_batch
                WHERE work_date=%s AND sequence=%s
                ORDER BY channel
            """, (work_date, int(sequence)))
            rows = cur.fetchall()
        conn.close()
    except Exception:
        return []
    return [{
        'work_date': r[0], 'sequence': r[1], 'channel': r[2],
        'row_count': r[3], 'source_filename': r[4], 'note': r[5],
        'created_at': r[6], 'updated_at': r[7],
    } for r in rows]


def list_keys_for_channel(channel: str, limit: int = 50) -> List[Dict]:
    """채널의 (work_date, sequence) 목록 — 드롭다운 용. row_count 같이.
    최신 우선 정렬.
    """
    ensure_schema()
    try:
        conn = pg.connect(autocommit=True)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT work_date, sequence, row_count, source_filename, updated_at
                FROM daone_pending_batch
                WHERE channel=%s
                ORDER BY work_date DESC, sequence DESC
                LIMIT %s
            """, (channel, int(limit)))
            rows = cur.fetchall()
        conn.close()
    except Exception:
        return []
    return [{
        'work_date': r[0], 'sequence': r[1], 'row_count': r[2],
        'source_filename': r[3], 'updated_at': r[4],
    } for r in rows]


def list_all(limit: int = 200) -> List[Dict]:
    """모든 batch 평면 메타 (rows 제외) — 통합 페이지의 평면 리스트.
    최신 우선.
    """
    ensure_schema()
    try:
        conn = pg.connect(autocommit=True)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT work_date, sequence, channel, row_count,
                       source_filename, note, created_at, updated_at
                FROM daone_pending_batch
                ORDER BY work_date DESC, sequence DESC, channel
                LIMIT %s
            """, (int(limit),))
            rows = cur.fetchall()
        conn.close()
    except Exception:
        return []
    return [{
        'work_date': r[0], 'sequence': r[1], 'channel': r[2],
        'row_count': r[3], 'source_filename': r[4], 'note': r[5],
        'created_at': r[6], 'updated_at': r[7],
    } for r in rows]


def list_all_sessions(limit: int = 50) -> List[Tuple[datetime.date, int]]:
    """모든 채널을 통틀어 (work_date, sequence) distinct — 통합 페이지의 selectbox 용.
    최신 우선.
    """
    ensure_schema()
    try:
        conn = pg.connect(autocommit=True)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT work_date, sequence
                FROM daone_pending_batch
                ORDER BY work_date DESC, sequence DESC
                LIMIT %s
            """, (int(limit),))
            rows = cur.fetchall()
        conn.close()
        return [(r[0], r[1]) for r in rows]
    except Exception:
        return []


def next_sequence_for_channel(channel: str,
                              work_date: Optional[datetime.date] = None) -> int:
    """해당 채널의 가장 큰 sequence + 1. 없으면 1.
    work_date 지정 시 그 날짜 기준 (다른 날짜 sequence 무시) — 매일 1차부터 시작.
    None이면 모든 날짜 통틀어.
    """
    ensure_schema()
    try:
        conn = pg.connect(autocommit=True)
        with conn.cursor() as cur:
            if work_date is None:
                cur.execute("""
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM daone_pending_batch
                    WHERE channel=%s
                """, (channel,))
            else:
                cur.execute("""
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM daone_pending_batch
                    WHERE channel=%s AND work_date=%s
                """, (channel, work_date))
            n = cur.fetchone()[0]
        conn.close()
        return int(n)
    except Exception:
        return 1


def delete(work_date: datetime.date, sequence: int, channel: str) -> bool:
    ensure_schema()
    try:
        conn = pg.connect()
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM daone_pending_batch
                WHERE work_date=%s AND sequence=%s AND channel=%s
            """, (work_date, int(sequence), channel))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False
