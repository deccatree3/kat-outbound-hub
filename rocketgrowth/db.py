"""SQLAlchemy 엔진/세션 생성."""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import load_config


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    cfg = load_config()
    # prepare_threshold=None: psycopg3 server-side prepared statement 생성 비활성화.
    # Supabase pgbouncer(transaction pooler) 가 연결 재사용 시 동일 statement name 충돌
    # (DuplicatePreparedStatement) 방지.
    return create_engine(
        cfg.database_url,
        pool_pre_ping=True,
        future=True,
        connect_args={"prepare_threshold": None},
    )


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False, future=True)


def get_session() -> Session:
    """단발성 세션 — 호출자가 with 블록에서 사용."""
    return _session_factory()()
