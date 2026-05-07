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
    """일본 출고 — 탭 1 신규주문 처리에서 수집된 데이터로 step2~6 진행.

    탭 1 에서 cu_qsm_rows / qoo10_detail_bytes / qoo10_brief_bytes 를 미리 채워둠 →
    여기서는 수집(step1) 생략하고 출고요청서 생성부터 시작.
    """
    from channels.qoo10_japan.page import (
        render_credentials_sidebar,
        _step2_outbound_generate, _step3_oms_upload_guide,
        _step4_collect_waybills, _step5_qsm_waybill_register,
        _step6_qsm_register_guide,
    )
    render_credentials_sidebar()

    det_ok = bool(st.session_state.get('qoo10_detail_bytes'))
    brief_ok = bool(st.session_state.get('qoo10_brief_bytes'))

    if not (det_ok and brief_ok):
        st.warning(
            "⚠️ **신규주문 데이터가 없습니다.** 먼저 **📤 신규주문 처리** 탭에서 "
            "QSM API 자동 또는 CSV 수동으로 주문을 수집하세요. "
            "수집된 데이터를 여기서 그대로 사용합니다 (재수집 불필요)."
        )
        return

    qsm_rows = st.session_state.get('cu_qsm_rows', [])
    detail_name = st.session_state.get('qoo10_detail_name', '')
    brief_name = st.session_state.get('qoo10_brief_name', '')
    mode_label = ('자동(API)' if st.session_state.get('cu_collect_mode') == 'api'
                  else '수동(CSV)')
    st.success(
        f"✅ 신규주문 처리 탭에서 수집됨 — 총 {len(qsm_rows)}건 ({mode_label}) · "
        f"`{detail_name}` / `{brief_name}`"
    )
    st.caption(
        "📜 단일 페이지 스크롤 — 출고요청서 생성 → KSE OMS 업로드(외부) → 송장 취합 → QSM 등록."
    )

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
        "📤 신규주문 처리", "국내 출고", "일본 출고",
    ])
    with tab_new:
        _tab_new_orders()
    with tab_kr:
        _tab_kr_outbound()
    with tab_jp:
        _tab_jp_outbound()
