"""로켓그로스 채널 통합 페이지 (네뉴/캐처스 공통).

호출 진입점:
    render_page(brand: str)  # brand = 'nenu' | 'cachers'

3 탭 구조:
  📋 1. 발주 계획   — 파일 업로드 + 발주 수량 산출 + 쿠팡 업로드 양식
  📦 2. 결과물 패키지 — 쿠팡 결과물 업로드 + 검수 + 물류센터 전달 패키지
  🚚 3. 송장 후처리   — 다원 송장 채번 파일 업로드 + 화주별 송장 양식

자매 프로젝트(nn-rocketgrowth_inventory) 의 단일 페이지를 3 탭으로 재구성.
실제 비즈니스 로직은 Phase C~E 단계별로 채워짐.
"""
from __future__ import annotations

import streamlit as st


BRAND_LABEL = {
    'nenu':    '네뉴',
    'cachers': '캐처스',
}


def _ensure_brand(brand: str) -> str:
    if brand not in BRAND_LABEL:
        raise ValueError(f"unknown brand: {brand}")
    return brand


def _tab_plan(brand: str):
    """탭 1: 발주 계획 (캐처스/네뉴 공통, 밀크런/택배 무관)."""
    from channels.rocketgrowth._tab_plan import render as _render_plan
    _render_plan(brand)


def _tab_package(brand: str):
    """탭 2: 결과물 패키지."""
    from channels.rocketgrowth._tab_package import render as _render_package
    _render_package(brand)


def _tab_invoice(brand: str):
    """탭 3: 송장 후처리."""
    from channels.rocketgrowth._tab_invoice import render as _render_invoice
    _render_invoice(brand)


def render_page(brand: str = 'nenu'):
    """채널 페이지 메인. dashboard.py 에서 채널 dispatch 시 호출.

    brand: 'nenu' | 'cachers'
    """
    brand = _ensure_brand(brand)

    st.caption(
        f"화주: **{BRAND_LABEL[brand]}** · 자매 프로젝트(nn-rocketgrowth_inventory) 이전 완료. "
        "운송 방식(밀크런/택배) 은 탭 2 의 메타 입력에서 선택."
    )

    tab_plan, tab_pack, tab_inv = st.tabs([
        "📋 1. 발주 계획",
        "📦 2. 쿠팡 입고생성",
        "🚚 3. 송장 후처리",
    ])
    with tab_plan:
        _tab_plan(brand)
    with tab_pack:
        _tab_package(brand)
    with tab_inv:
        _tab_invoice(brand)
