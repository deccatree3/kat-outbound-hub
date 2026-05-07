"""
Postgres 공통 헬퍼. dashboard/loader/notifier 모두 여기를 사용.

Connection Pool 활성화 — psycopg_pool 가 설치된 환경에서는
연결 재사용으로 매 호출 TCP 핸드셰이크 비용 제거.
"""
import os
import json
import threading

import psycopg

try:
    from psycopg_pool import ConnectionPool
    _POOL_AVAILABLE = True
except Exception:
    ConnectionPool = None
    _POOL_AVAILABLE = False


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_CFG = os.path.join(BASE_DIR, "config.json")


def get_dsn() -> str:
    """환경변수 우선, 없으면 config.json"""
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        return dsn
    if os.path.exists(APP_CFG):
        with open(APP_CFG, "r", encoding="utf-8") as f:
            return json.load(f).get("database_url", "")
    return ""


# ─── ConnectionPool (싱글턴) ─────────────────────────────────
_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    """프로세스 1개 ConnectionPool. 없으면 생성. psycopg_pool 미설치면 None."""
    global _pool
    if not _POOL_AVAILABLE:
        return None
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        dsn = get_dsn()
        if not dsn:
            return None
        _pool = ConnectionPool(
            conninfo=dsn,
            min_size=1, max_size=5,
            kwargs={'prepare_threshold': None},  # Supabase pooler 호환
            open=True,
        )
        return _pool


class _PooledConn:
    """psycopg.Connection wrapper. close() → pool.putconn 으로 redirect.

    호출자는 기존처럼 conn.close() 사용. Pool 이 connection 재사용.
    """
    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool
        self._returned = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            try:
                self._conn.rollback()
            except Exception:
                pass
        self.close()
        return False

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        if self._returned:
            return
        self._returned = True
        try:
            self._pool.putconn(self._conn)
        except Exception:
            try:
                self._conn.close()
            except Exception:
                pass


def connect(**kwargs) -> psycopg.Connection:
    """Connection 반환. Pool 사용 가능하면 pool 에서 발급.
    호출자가 close() 하면 pool 로 반환됨 (래퍼 통해 putconn).
    """
    pool = _get_pool()
    if pool is not None:
        try:
            conn = pool.getconn()
        except Exception:
            conn = None
        if conn is not None:
            # 이전 사용자 상태가 남을 수 있어 autocommit 명시적 초기화
            try:
                conn.autocommit = bool(kwargs.get('autocommit', False))
            except Exception:
                pass
            for k, v in kwargs.items():
                if k == 'autocommit':
                    continue
                try:
                    setattr(conn, k, v)
                except Exception:
                    pass
            return _PooledConn(conn, pool)
    # Fallback: pool 미사용 (기존 동작)
    dsn = get_dsn()
    if not dsn:
        raise RuntimeError("DATABASE_URL이 설정되지 않았습니다.")
    kwargs.setdefault("prepare_threshold", None)
    return psycopg.connect(dsn, **kwargs)


def query_df(sql: str, params=None, conn=None):
    """쿼리를 DataFrame으로 반환 (pandas 필요)"""
    import pandas as pd
    close = False
    if conn is None:
        conn = connect(autocommit=True)
        close = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            if cur.description is None:
                return pd.DataFrame()
            cols = [d.name for d in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        if close:
            conn.close()
