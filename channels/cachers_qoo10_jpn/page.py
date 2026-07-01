"""
[캐처스] Qoo10-일본 채널.

2 탭 구성:
  📤 1. 신규주문 처리 — QSM API 가져오기 → 매핑 활성여부 lookup 으로 분류
  2. 일본 출고       — 출고요청서 생성 → KSE OMS 일본 업로드 (외부) → 송장 받음 →
                       QSM 등록 (qoo10_japan step2~6 단일 페이지 형태)
"""
import streamlit as st


CHANNEL_KEY = 'cachers_qoo10_jpn'
CHANNEL_JP = 'qoo10_japan'


def _tab_new_orders():
    from channels.cachers_qoo10_jpn._tab_new_orders import render
    render()


def _render_resume_section():
    """확정된 brief 드롭다운 — 선택 시 session 로드. (탭 1 '주문수집 확정' 한 batch)."""
    from channels.cachers_qoo10_jpn._brief_picker import render_brief_picker
    render_brief_picker(key_prefix='cu_jp', title="발주계획 선택")


def _tab_jp_outbound():
    """일본 출고 — 두 진입 모드 지원.

    A. 신규 (detail+brief 둘 다 있음): ① 출고요청서 생성 → ② OMS 업로드 → ③ 송장 취합 → ④ 송장 등록 → ⑤ 안내
    B. 이어서 (brief 만 있음, detail 없음): ③ 송장 취합 → ④ 송장 등록 → ⑤ 안내

    탭 1 에서 수집하면 A. 시간차로 다시 들어오면 '기존 작업 이어서' 로 B 선택.
    """
    from channels.cachers_qoo10_jpn._stepper import (
        render_credentials_sidebar, render_kse_credentials_sidebar,
        _step2_outbound_generate, _step3_oms_upload_guide,
        _step4_collect_waybills, _step5_qsm_waybill_register,
        _step6_qsm_register_guide,
    )
    render_credentials_sidebar()
    render_kse_credentials_sidebar()

    # 기존 작업 이어서 (DB 의 미완료 brief 선택)
    _render_resume_section()

    det_ok = bool(st.session_state.get('qoo10_detail_bytes'))
    brief_ok = bool(st.session_state.get('qoo10_brief_bytes'))

    if not brief_ok:
        st.warning(
            "⚠️ **신규주문 데이터가 없습니다.** 다음 중 하나로 진행하세요.\n\n"
            "  • **신규**: 📤 신규주문 처리 탭에서 QSM 자동/수동 수집\n\n"
            "  • **이어서**: 위 '기존 작업 이어서' 에서 미완료 brief 선택"
        )
        return

    qsm_rows = st.session_state.get('cu_qsm_rows', [])
    detail_name = st.session_state.get('qoo10_detail_name', '')
    brief_name = st.session_state.get('qoo10_brief_name', '')
    wd = st.session_state.get('qoo10_brief_work_date')
    sq = st.session_state.get('qoo10_brief_sequence')
    session_tag = (f"**{wd.strftime('%Y-%m-%d')} / {sq}차** · "
                   if wd and sq else "")

    if det_ok:
        # A. 신규 흐름 — ① 부터
        mode_label = ('자동(API)' if st.session_state.get('cu_collect_mode') == 'api'
                      else '수동(CSV)')
        st.success(
            f"✅ {session_tag}신규주문 — 총 {len(qsm_rows)}건 ({mode_label}) · "
            f"`{brief_name}`"
        )
        st.markdown("---")
        _step2_outbound_generate()  # ①
        st.markdown("---")
        _step3_oms_upload_guide()   # ②
        st.markdown("---")
    else:
        # B. 이어서 흐름 — brief 만 있음
        st.info(
            f"📜 {session_tag}**기존 작업 이어서** — `{brief_name}` 로드됨. "
            "①(출고요청서 생성)/②(OMS 업로드)는 이미 완료된 것으로 간주하고 **③ 송장 취합부터** 진행."
        )
        _bid = st.session_state.get('qoo10_brief_id')
        if _bid:
            from qoo10 import generator as _qgen
            _saved_ob = _qgen.load_brief_outbound(_bid)
            if _saved_ob:
                _ob_bytes, _ob_name = _saved_ob
                st.download_button(
                    f"📥 출고요청서 재다운로드 ({_ob_name})",
                    data=_ob_bytes,
                    file_name=_ob_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch",
                    key="cu_jp_resume_redownload_outbound",
                    help="① 단계에서 생성·저장된 KSE 출고요청서. OMS 재업로드가 필요할 때 사용.",
                )
            else:
                st.caption(
                    "ℹ️ 이 brief 에 저장된 출고요청서가 없습니다 "
                    "(저장 기능 도입 이전 작업이거나 ① 단계 미실행)."
                )
        st.markdown("---")

    _step4_collect_waybills()       # ③
    st.markdown("---")
    _step5_qsm_waybill_register()   # ④
    st.markdown("---")
    _step6_qsm_register_guide()     # ⑤


def render_page():
    tab_new, tab_jp = st.tabs([
        "📤 1. 신규주문 처리",
        "2. 일본 출고",
    ])
    with tab_new:
        _tab_new_orders()
    with tab_jp:
        _tab_jp_outbound()
