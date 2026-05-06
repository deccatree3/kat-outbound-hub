"""
[캐처스] Qoo10 통합 채널.

3 탭 구성:
  📤 신규주문 처리 — QSM API 가져오기 → 매핑 활성여부 lookup 으로 KR/JP 분류
                     → KR 분기는 SetSellerCheckYN_V2 호출 (배송준비 전이)
                     → JP 분기는 일본 출고 탭으로
  🇰🇷 국내 출고     — KSE OMS 패킹리스트 업로드 → 다원 발주서/패킹리스트/부착문서
                     (cachers_qoo10_kr 페이지 그대로 재사용)
  🇯🇵 일본 출고     — 출고요청서 생성 → KSE OMS 일본 업로드 (외부) → 송장 받음 →
                     QSM 등록 (qoo10_japan step2~6 단일 페이지 형태)
"""
import streamlit as st


CHANNEL_KEY = 'cachers_qoo10'
CHANNEL_JP = 'qoo10_japan'
CHANNEL_KR = 'cachers_qoo10_kr'


def _tab_new_orders():
    from channels.cachers_qoo10._tab_new_orders import render
    render()


def _tab_kr_outbound():
    """국내 출고 — 기존 cachers_qoo10_kr 페이지 재사용."""
    from channels.cachers_qoo10_kr.page import render_page as _kr_render
    _kr_render()


def _tab_jp_outbound():
    """일본 출고 — 기존 qoo10_japan render_page 재사용 (C-3a).
    추후 stepper → 단일 페이지 스크롤 형태로 재구성 (C-3b).
    """
    st.caption(
        "⚠️ 임시: 기존 6 단계 stepper 그대로. step1(QSM 가져오기)는 신규주문 처리 탭으로 이미 이전. "
        "step2 부터 진행하세요. (단일 페이지 스크롤 재구성은 다음 단계 예정)"
    )
    from channels.qoo10_japan.page import render_page as _jp_render
    _jp_render()


def render_page():
    tab_new, tab_kr, tab_jp = st.tabs([
        "📤 신규주문 처리", "🇰🇷 국내 출고", "🇯🇵 일본 출고",
    ])
    with tab_new:
        _tab_new_orders()
    with tab_kr:
        _tab_kr_outbound()
    with tab_jp:
        _tab_jp_outbound()
