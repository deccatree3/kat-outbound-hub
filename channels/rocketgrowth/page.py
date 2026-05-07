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
    st.markdown("### 📋 발주 계획")
    st.caption(
        "쿠팡 재고 health + WMS 재고 파일 업로드 → 발주 수량 자동 산출 → 임시저장 → "
        "쿠팡 업로드용 양식 생성."
    )
    st.info("🚧 Phase C 에서 자매 프로젝트의 발주 계획 로직 이전 예정.")


def _tab_package(brand: str):
    """탭 2: 결과물 패키지 (운송방식 분기 + 화주 분기)."""
    st.markdown("### 📦 결과물 패키지")
    st.caption(
        "운송 방식(밀크런/택배) 선택 → 쿠팡 결과물 PDF 업로드(부착/동봉/바코드) → "
        "검수 → 물류센터 전달 패키지 생성."
    )

    # 운송 방식 선택 (placeholder)
    shipment = st.radio(
        "운송 방식",
        options=["밀크런", "택배"],
        horizontal=True,
        key=f"rg_{brand}_shipment",
        help="발주 수량 확정 후 결과물 양식 분기에 영향. 부착문서 양식이 다름.",
    )

    st.markdown(f"**선택**: {shipment}")
    st.info("🚧 Phase D 에서 운송별 결과물 + 검수 로직 이전 예정.")

    # 화주별 출고요청서 결과물 미리보기 자리
    if brand == 'nenu':
        st.caption("→ 물류센터 전달 패키지에 **이지어드민 발주서.xls** 포함 예정.")
    else:
        st.caption("→ 물류센터 전달 패키지에 **다원 출고요청서.xlsx** 포함 예정.")


def _tab_invoice(brand: str):
    """탭 3: 송장 후처리 (화주 분기)."""
    st.markdown("### 🚚 송장 후처리")
    st.caption(
        "다원에서 송장 채번한 파일 업로드 → 화주별 송장 결과물 생성. "
        "쿠팡 송장 업로드 양식은 Phase F 후속."
    )

    if brand == 'nenu':
        st.info(
            "🚧 Phase E — 다원 송장 채번 파일 → **이지어드민 송장 업로드 양식.xlsx** 생성."
        )
    else:
        st.info(
            "🚧 Phase E — 캐처스는 이지어드민 미사용. "
            "쿠팡 송장 등록 양식 (Phase F) 외 결과물 없음 — 다원 송장 받으면 직접 처리."
        )


def render_page(brand: str = 'nenu'):
    """채널 페이지 메인. dashboard.py 에서 채널 dispatch 시 호출.

    brand: 'nenu' | 'cachers'
    """
    brand = _ensure_brand(brand)

    st.caption(f"화주: **{BRAND_LABEL[brand]}** · 자매 프로젝트(nn-rocketgrowth_inventory) 이전 중.")

    tab_plan, tab_pack, tab_inv = st.tabs([
        "📋 1. 발주 계획",
        "📦 2. 결과물 패키지",
        "🚚 3. 송장 후처리",
    ])
    with tab_plan:
        _tab_plan(brand)
    with tab_pack:
        _tab_package(brand)
    with tab_inv:
        _tab_invoice(brand)
