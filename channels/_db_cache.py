"""
자주 호출되는 read-only DB 함수의 Streamlit 캐싱 wrapper.

매 위젯 클릭 시 페이지가 처음부터 재실행 → 같은 DB 쿼리 반복 = 느린 반응.
60초 TTL 로 동일 인자 호출 결과 재사용.

쓰기 작업(upsert/delete) 후 `invalidate_all()` 호출해서 캐시 무효화.
"""
import streamlit as st

from db import daone_batch as _b
from db import mapping as _m
from qoo10 import generator as qgen


_TTL = 60  # 초


# ─── daone_pending_batch ───
@st.cache_data(ttl=_TTL, show_spinner=False)
def list_keys_for_channel(channel: str):
    return _b.list_keys_for_channel(channel, limit=50)


@st.cache_data(ttl=_TTL, show_spinner=False)
def next_sequence_for_channel(channel: str, work_date):
    return _b.next_sequence_for_channel(channel, work_date=work_date)


@st.cache_data(ttl=_TTL, show_spinner=False)
def list_all_batches(limit: int = 200):
    return _b.list_all(limit=limit)


# ─── channel_product_mapping ───
@st.cache_data(ttl=_TTL, show_spinner=False)
def load_mapping(channel: str, active_only: bool = True):
    return _m.load_for_channel(channel, active_only=active_only)


@st.cache_data(ttl=_TTL, show_spinner=False)
def list_all_mappings(channel=None, search=None):
    return _m.list_all(channel=channel, search=search)


@st.cache_data(ttl=_TTL, show_spinner=False)
def count_mappings_by_channel():
    return _m.count_by_channel()


# ─── qoo10_pending_brief ───
@st.cache_data(ttl=_TTL, show_spinner=False)
def qoo10_brief_keys():
    return qgen.list_brief_keys(limit=50)


@st.cache_data(ttl=_TTL, show_spinner=False)
def qoo10_next_brief_sequence(work_date):
    return qgen.next_brief_sequence(work_date)


@st.cache_data(ttl=_TTL, show_spinner=False)
def qoo10_pending_briefs():
    return qgen.list_pending_briefs(include_consumed=False, limit=20)


def invalidate_all():
    """쓰기 후 호출 — 모든 cached 함수 무효화."""
    list_keys_for_channel.clear()
    next_sequence_for_channel.clear()
    list_all_batches.clear()
    load_mapping.clear()
    list_all_mappings.clear()
    count_mappings_by_channel.clear()
    qoo10_brief_keys.clear()
    qoo10_next_brief_sequence.clear()
    qoo10_pending_briefs.clear()
