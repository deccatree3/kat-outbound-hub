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


def _render_resume_section():
    """미완료 brief 목록 → 선택 시 brief만 session에 복원 (detail 없이도 송장 취합 가능).

    오전에 출고요청서 생성/OMS 업로드 후, 오후에 와서 송장만 등록하는 시나리오 지원.
    brief는 ① 단계에서 DB(`qoo10_pending_brief`)에 자동 저장됨.
    """
    from qoo10 import generator as qgen
    try:
        pending = qgen.list_pending_briefs(include_consumed=False, limit=10)
    except Exception:
        return
    if not pending:
        return

    with st.expander(
        f"🔄 기존 작업 이어서 ({len(pending)}건 미완료 brief — 오전 작업 → 오후 송장 등록)",
        expanded=False,
    ):
        st.caption(
            "출고요청서 생성 시 brief 가 자동 저장됨. "
            "여기서 선택하면 detail 없이도 송장 취합/등록(③~⑤) 가능."
        )
        options = [None] + [p['id'] for p in pending]
        label_map = {
            p['id']: (
                f"{p['created_at'].strftime('%m-%d %H:%M') if p['created_at'] else '시간미상'}"
                f" · {p['file_name']} · 주문 {p['cart_count']}건"
            )
            for p in pending
        }
        sel = st.selectbox(
            "미완료 brief 선택",
            options=options,
            format_func=lambda i: '(선택 안함)' if i is None else label_map[i],
            key="cu_jp_resume_select",
        )
        if sel is not None and st.button(
            "📥 이 작업 이어서 시작", key="cu_jp_resume_btn", type="primary",
        ):
            try:
                content, fname = qgen.load_pending_brief(sel)
            except Exception as ex:
                st.error(f"로드 실패: {ex}")
                return
            st.session_state['qoo10_brief_bytes'] = content
            st.session_state['qoo10_brief_name'] = fname
            st.session_state['qoo10_brief_id'] = sel
            # detail 은 없음 → ③부터 진행
            st.session_state.pop('qoo10_detail_bytes', None)
            st.session_state.pop('qoo10_detail_name', None)
            st.success(f"✅ {fname} 로드. 송장 취합부터 진행하세요.")
            st.rerun()


def _tab_jp_outbound():
    """일본 출고 — 두 진입 모드 지원.

    A. 신규 (detail+brief 둘 다 있음): ① 출고요청서 생성 → ② OMS 업로드 → ③ 송장 취합 → ④ 송장 등록 → ⑤ 안내
    B. 이어서 (brief 만 있음, detail 없음): ③ 송장 취합 → ④ 송장 등록 → ⑤ 안내

    탭 1 에서 수집하면 A. 시간차로 다시 들어오면 '기존 작업 이어서' 로 B 선택.
    """
    from channels.qoo10_japan.page import (
        render_credentials_sidebar,
        _step2_outbound_generate, _step3_oms_upload_guide,
        _step4_collect_waybills, _step5_qsm_waybill_register,
        _step6_qsm_register_guide,
    )
    render_credentials_sidebar()

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

    if det_ok:
        # A. 신규 흐름 — ① 부터
        mode_label = ('자동(API)' if st.session_state.get('cu_collect_mode') == 'api'
                      else '수동(CSV)')
        st.success(
            f"✅ 신규주문 — 총 {len(qsm_rows)}건 ({mode_label}) · "
            f"`{detail_name}` / `{brief_name}`"
        )
        st.markdown("---")
        _step2_outbound_generate()  # ①
        st.markdown("---")
        _step3_oms_upload_guide()   # ②
        st.markdown("---")
    else:
        # B. 이어서 흐름 — brief 만 있음
        st.info(
            f"📜 **기존 작업 이어서** — `{brief_name}` 로드됨. "
            "①(출고요청서 생성)/②(OMS 업로드)는 이미 완료된 것으로 간주하고 **③ 송장 취합부터** 진행."
        )
        st.markdown("---")

    _step4_collect_waybills()       # ③
    st.markdown("---")
    _step5_qsm_waybill_register()   # ④
    st.markdown("---")
    _step6_qsm_register_guide()     # ⑤


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
