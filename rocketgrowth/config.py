"""rocketgrowth 모듈 설정 로더 — kat-outbound-hub 통합 버전.

DB 접속 우선순위:
  1) DATABASE_URL 환경변수 (Streamlit Cloud secrets → env 승격됨)
  2) repo root config.json 의 database_url (raw psycopg 형식)
  3) Streamlit secrets [database] 섹션 (host/port/user/password/dbname)

raw psycopg DSN (`postgresql://...`) 은 자동으로 `postgresql+psycopg://...` 로 변환.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus


@dataclass(frozen=True)
class AppConfig:
    database_url: str
    # 대시보드 경고 임계값
    low_stock_days_threshold: int = 14
    near_expiry_ratio_threshold: float = 0.3
    # 입고 계산 엔진 파라미터
    lead_time_days: int = 7
    target_cover_days: int = 14
    velocity_alpha: float = 0.4
    overstock_days: int = 35
    reproduction_lead_days: int = 28
    # 멀티 업체
    default_company_name: str = "서현"
    default_shipment_type: str = "milkrun"  # milkrun | parcel
    pallet_size_boxes: int = 19


def _to_sqlalchemy_url(raw: str) -> str:
    """raw psycopg 형식 (`postgresql://`) 을 SQLAlchemy 형식 (`postgresql+psycopg://`) 으로 변환."""
    if not raw:
        return raw
    if raw.startswith("postgresql+"):
        return raw  # 이미 dialect 명시됨
    if raw.startswith("postgres://"):
        return "postgresql+psycopg://" + raw[len("postgres://"):]
    if raw.startswith("postgresql://"):
        return "postgresql+psycopg://" + raw[len("postgresql://"):]
    return raw


def _build_url_from_parts(db: dict) -> str | None:
    host = db.get("host")
    if not host:
        return None
    user = db.get("user", "postgres")
    password = db.get("password", "")
    port = int(db.get("port", 5432))
    dbname = db.get("dbname", "postgres")
    driver = db.get("driver", "postgresql+psycopg")
    return f"{driver}://{quote_plus(str(user))}:{quote_plus(str(password))}@{host}:{port}/{dbname}"


def load_config() -> AppConfig:
    # 1) DATABASE_URL 환경변수
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return AppConfig(database_url=_to_sqlalchemy_url(env_url))

    # 2) repo root config.json (kat-outbound-hub 표준)
    repo_root = Path(__file__).resolve().parent.parent
    cfg_path = repo_root / "config.json"
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            url = data.get("database_url", "")
            if url:
                return AppConfig(database_url=_to_sqlalchemy_url(url))
        except Exception:
            pass

    # 3) Streamlit secrets (CLI/테스트 실행 시에도)
    try:
        import streamlit as st  # type: ignore
        if hasattr(st, "secrets"):
            if "database" in st.secrets:
                db_section = dict(st.secrets["database"])
                url = db_section.get("url")
                if not url:
                    url = _build_url_from_parts(db_section)
                if url:
                    return AppConfig(database_url=_to_sqlalchemy_url(url))
            if "DATABASE_URL" in st.secrets:
                return AppConfig(
                    database_url=_to_sqlalchemy_url(str(st.secrets["DATABASE_URL"]))
                )
    except Exception:
        pass

    raise RuntimeError(
        "DB 설정이 없습니다. DATABASE_URL 환경변수 또는 config.json / "
        ".streamlit/secrets.toml 의 database 섹션을 확인하세요."
    )
