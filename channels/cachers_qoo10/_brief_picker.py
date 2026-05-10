"""Qoo10 brief 선택 picker — 탭 1 에서 '주문수집 확정' 한 batch 를 탭 2/3 에서 로드.

탭 1 (cachers_qoo10/_tab_new_orders) 에서 '주문수집 확정' 클릭 시 brief 가
DB(qoo10_pending_brief, status='collected') 에 저장됨. 이 picker 는 그 brief 들을
드롭다운으로 노출 → 사용자가 선택 → session 에 로드.

탭 2 (cachers_qoo10_kr): 국내 출고 작업 시 발주계획 컨텍스트 표시
탭 3 (cachers_qoo10/_tab_jp_outbound): 일본 출고 작업 시 brief 로드 후 ③ 송장 취합부터 진행
"""
import streamlit as st


STATUS_LABELS = {
    'collected': '📋 주문수집',
}


def format_brief_label(brief: dict) -> str:
    """발주계획 라벨 — '#[id] · [상태] · [YYYY-MM-DD] / [N]차 · [건수]건 · [파일명]'."""
    status = brief.get('status') or 'collected'
    status_label = STATUS_LABELS.get(status, status)
    wd = brief.get('work_date')
    sq = brief.get('sequence')
    wd_str = wd.strftime('%Y-%m-%d') if wd else '—'
    sq_str = f"{sq}차" if sq else '—'
    cnt = brief.get('cart_count', 0)
    fname = brief.get('file_name', '')
    fname_short = (fname[:30] + '…') if len(fname) > 30 else fname
    return (
        f"#{brief['id']} · {status_label} · "
        f"{wd_str} / {sq_str} · "
        f"{cnt}건"
        + (f" · {fname_short}" if fname_short else "")
    )


def render_brief_picker(
    key_prefix: str,
    title: str = "발주계획 선택",
    clear_detail_on_load: bool = True,
) -> dict | None:
    """확정된 brief 드롭다운. 선택 시 session 에 로드. 반환: 선택된 brief dict 또는 None."""
    from qoo10 import generator as qgen
    try:
        pending = qgen.list_pending_briefs(include_consumed=False, limit=20)
    except Exception:
        return None
    if not pending:
        st.caption(f"📭 확정된 발주계획 없음. (탭 1 에서 '주문수집 확정' 먼저)")
        return None

    SENTINEL = -1
    labels = {SENTINEL: "— 발주계획 선택 —"}
    for p in pending:
        labels[p['id']] = format_brief_label(p)

    sel_key = f"{key_prefix}_brief_picker_sel"
    active_key = f"{key_prefix}_brief_picker_active"
    cur_session_id = st.session_state.get('qoo10_brief_id')
    # session 에 이미 있는 brief 가 목록에 있으면 selectbox default = 그 id
    if active_key not in st.session_state and cur_session_id in labels:
        st.session_state[sel_key] = cur_session_id
        st.session_state[active_key] = cur_session_id

    options = [SENTINEL] + [p['id'] for p in pending]
    sel = st.selectbox(
        title,
        options=options,
        format_func=lambda o: labels.get(o, str(o)),
        index=0,
        key=sel_key,
    )

    if sel == SENTINEL:
        return None

    picked = next((p for p in pending if p['id'] == sel), None)
    if not picked:
        return None

    # 사용자가 다른 brief 로 변경 → 로드
    if cur_session_id != sel:
        try:
            content, fname = qgen.load_pending_brief(sel)
        except Exception as ex:
            st.error(f"brief #{sel} 로드 실패: {ex}")
            return None
        st.session_state['qoo10_brief_bytes'] = content
        st.session_state['qoo10_brief_name'] = fname
        st.session_state['qoo10_brief_id'] = sel
        st.session_state['qoo10_brief_work_date'] = picked.get('work_date')
        st.session_state['qoo10_brief_sequence'] = picked.get('sequence')
        if clear_detail_on_load:
            st.session_state.pop('qoo10_detail_bytes', None)
            st.session_state.pop('qoo10_detail_name', None)
        st.session_state[active_key] = sel
        st.rerun()

    return picked
