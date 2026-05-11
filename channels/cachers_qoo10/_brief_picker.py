"""Qoo10 brief 선택 picker — 탭 1/2/3 통합 UI.

탭 1 (신규주문 처리): allow_new=True — '+ 신규 작업' + 기존 brief 목록
탭 2 (국내 출고)    : allow_new=False — 기존 brief 만
탭 3 (일본 출고)    : allow_new=False — 기존 brief 만
"""
import streamlit as st


STATUS_LABELS = {
    'collected': '📋 주문수집',
}


def format_brief_label(brief: dict) -> str:
    """발주계획 라벨 — '#[id] · [상태] · [YYYY-MM-DD 생성일] · [건수]건'."""
    status = brief.get('status') or 'collected'
    status_label = STATUS_LABELS.get(status, status)
    wd = brief.get('work_date')
    wd_str = wd.strftime('%Y-%m-%d') if wd else '—'
    cnt = brief.get('cart_count', 0)
    return f"#{brief['id']} · {status_label} · {wd_str} · {cnt}건"


def _render_delete_expander(pending, key_prefix):
    """과거 작업 삭제 expander — 체크박스 다중 선택 + 일괄 삭제 버튼."""
    from qoo10 import generator as qgen
    from channels import _db_cache as _cache

    with st.expander(f"🗑 과거 작업 삭제 ({len(pending)}건)", expanded=False):
        st.caption("체크 후 '선택 삭제' 버튼 — DB에서 즉시 삭제됨 (복구 불가).")
        to_delete = []
        for p in pending:
            label = format_brief_label(p)
            chk_key = f"{key_prefix}_briefdel_chk_{p['id']}"
            if st.checkbox(label, key=chk_key, value=False):
                to_delete.append(p['id'])
        btn_key = f"{key_prefix}_briefdel_btn"
        if st.button(
            f"🗑 선택 삭제 ({len(to_delete)}건)",
            key=btn_key, disabled=(not to_delete), type="primary", width="stretch",
        ):
            ok = 0
            fail = []
            for bid in to_delete:
                try:
                    qgen.delete_pending_brief(bid)
                    ok += 1
                    st.session_state.pop(f"{key_prefix}_briefdel_chk_{bid}", None)
                except Exception as ex:
                    fail.append(f"#{bid}: {ex}")
            if ok:
                _cache.invalidate_all()
                st.success(f"✅ {ok}건 삭제됨.")
            if fail:
                st.error("삭제 실패: " + ", ".join(fail))
            if ok:
                st.session_state.pop(f"{key_prefix}_brief_picker_sel", None)
                st.rerun()


def render_brief_picker(
    key_prefix: str,
    title: str = "발주계획 선택",
    clear_detail_on_load: bool = True,
    allow_new: bool = False,
) -> dict | None:
    """확정된 brief 드롭다운. 선택 시 session 에 로드.

    Args:
      allow_new: True 면 '+ 신규 작업 (오늘 / N차)' 옵션 첫 번째로 추가.

    반환:
      - None: sentinel 선택 (대기)
      - {'is_new': True, 'work_date': today, 'sequence': next_seq}: 신규 선택
      - brief dict: 기존 brief 선택 (session 에 load 완료)
    """
    from qoo10 import generator as qgen
    from utils.timezone import kst_today
    try:
        pending = qgen.list_pending_briefs(include_consumed=False, limit=20)
    except Exception:
        pending = []

    today = kst_today()

    NEW = '__new__'
    SENTINEL = -1
    labels = {SENTINEL: "— 발주계획 선택 —"}
    next_seq = qgen.next_brief_sequence(today) if allow_new else 1
    if allow_new:
        labels[NEW] = f"+ 신규 작업 (오늘 / {next_seq}차)"
    for p in pending:
        labels[p['id']] = format_brief_label(p)

    options: list = []
    if allow_new:
        options.append(NEW)
    options.append(SENTINEL)
    options += [p['id'] for p in pending]

    sel_key = f"{key_prefix}_brief_picker_sel"
    active_key = f"{key_prefix}_brief_picker_active"
    cur_session_id = st.session_state.get('qoo10_brief_id')
    if active_key not in st.session_state and cur_session_id in labels:
        st.session_state[sel_key] = cur_session_id
        st.session_state[active_key] = cur_session_id

    sel = st.selectbox(
        title,
        options=options,
        format_func=lambda o: labels.get(o, str(o)),
        index=0,
        key=sel_key,
    )

    # 과거 작업 삭제 expander (sentinel/new 모드에서만 노출)
    if pending and sel in (SENTINEL, NEW):
        _render_delete_expander(pending, key_prefix)

    if sel == NEW:
        return {'is_new': True, 'work_date': today, 'sequence': next_seq}
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
