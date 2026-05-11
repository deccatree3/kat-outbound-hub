"""
[캐처스] Qoo10-국내 채널.

2 탭 구성:
  📤 1. 신규주문 처리 — QSM API 가져오기 → 매핑 활성여부 lookup 으로 KR/JP 분류
                       → KR 분기는 SetSellerCheckYN_V2 호출 (배송준비 전이)
  🇰🇷 2. 국내 출고     — KSE OMS 패킹리스트 업로드 → 다원 발주서/패킹리스트/부착문서
                       (cachers_qoo10_kr 페이지 그대로 재사용)
"""
import streamlit as st


CHANNEL_KEY = 'cachers_qoo10_korea'
CHANNEL_KR = 'cachers_qoo10_kr'


def _tab_new_orders():
    from channels.cachers_qoo10_korea._tab_new_orders import render
    render()


def _tab_kr_outbound():
    """국내 출고 — 기존 cachers_qoo10_kr 페이지 재사용."""
    from channels.cachers_qoo10_kr.page import render_page as _kr_render
    _kr_render()


def render_page():
    tab_new, tab_kr = st.tabs([
        "📤 1. 신규주문 처리",
        "2. 국내 출고",
    ])
    with tab_new:
        _tab_new_orders()
    with tab_kr:
        _tab_kr_outbound()
