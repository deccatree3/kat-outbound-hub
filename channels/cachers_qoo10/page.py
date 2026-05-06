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
    """일본 출고 — qoo10_japan step1~6 함수를 단일 페이지 스크롤로 호출 (C-3b).

    UI: stepper 제거 + 섹션별 마크다운 헤더 + 스크롤.
    데이터 흐름은 qoo10_japan session_state 키 (qoo10_detail_bytes 등) 그대로 — 신규주문 처리 탭과 별개 (다음 단계에서 통합).
    """
    from channels.qoo10_japan.page import (
        render_credentials_sidebar,
        _step1_qsm_collect, _step2_outbound_generate,
        _step3_oms_upload_guide, _step4_collect_waybills,
        _step5_qsm_waybill_register, _step6_qsm_register_guide,
    )
    # 사이드바 인증키
    render_credentials_sidebar()

    st.caption(
        "📜 단일 페이지 스크롤 — 위에서 아래로 순차 작업. "
        "신규주문 처리 탭과 별개 흐름 (자동 데이터 전달은 다음 단계 예정)."
    )

    st.markdown("---")
    _step1_qsm_collect()

    st.markdown("---")
    _step2_outbound_generate()

    st.markdown("---")
    _step3_oms_upload_guide()

    st.markdown("---")
    _step4_collect_waybills()

    st.markdown("---")
    _step5_qsm_waybill_register()

    st.markdown("---")
    _step6_qsm_register_guide()


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
