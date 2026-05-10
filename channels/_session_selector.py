"""
채널 페이지 공통 — 작업일/차수 selector + 삭제 UI.

모든 채널이 동일한 UI/UX 사용. 데이터 백엔드 차이는 `WorkSessionAdapter` 로 추상화:
  - 다원 발주서 채널 (domestic / cachers_qoo10_kr / cachers_makers): daone_pending_batch
  - Qoo10 일본 채널 (qoo10_japan brief): qoo10_pending_brief

드롭다운 옵션:
  ➕ 신규 작업 (today / next_seq 자동)
  ─ 기존 작업 ─
  YYYY-MM-DD / N차 - HH:MM — N행 · 파일명
  ...

신규 선택 시: 작업일/차수 편집 가능 (default = today / next_seq)
기존 선택 시: 작업일/차수 read-only

반환: dict {'work_date', 'sequence', 'is_new', 'existing_meta'}
"""
import datetime
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import streamlit as st

from utils.timezone import kst_today


NEW_OPTION_KEY = '__new__'


@dataclass
class WorkSessionAdapter:
    """데이터 백엔드 추상화. 채널이 다른 테이블 쓰면 다른 adapter 주입.

    list_history(channel) → [{'work_date', 'sequence', 'row_count',
                              'source_filename', 'work_time'}]
    next_sequence(channel, work_date) → int
    delete_one(work_date, sequence, channel) → bool
    """
    list_history: Callable[[str], List[Dict]]
    next_sequence: Callable[[str, datetime.date], int]
    delete_one: Callable[[datetime.date, int, str], bool]


def _default_daone_adapter() -> WorkSessionAdapter:
    """다원 발주서 채널용 default adapter (daone_pending_batch)."""
    from db import daone_batch as _b
    from channels import _db_cache as _cache
    def _delete(wd, sq, ch):
        ok = _b.delete(wd, sq, ch)
        if ok:
            _cache.invalidate_all()
        return ok
    return WorkSessionAdapter(
        list_history=lambda ch: _cache.list_keys_for_channel(ch),
        next_sequence=lambda ch, wd: _cache.next_sequence_for_channel(ch, wd),
        delete_one=_delete,
    )


def render_work_session_selector(channel: str, key_prefix: str,
                                  adapter: Optional[WorkSessionAdapter] = None) -> Dict:
    """채널 페이지 상단에 표시. 반환: {work_date, sequence, is_new, existing_meta}.

    key_prefix: 채널 페이지 안에서 위젯 key 충돌 방지용.
    adapter: 데이터 백엔드. None 이면 daone_pending_batch (다원 발주서 채널).
    """
    if adapter is None:
        adapter = _default_daone_adapter()
    history = adapter.list_history(channel)
    today = kst_today()
    next_seq = adapter.next_sequence(channel, today)

    options: List = [NEW_OPTION_KEY] + [(h['work_date'], h['sequence']) for h in history]
    history_by_key = {(h['work_date'], h['sequence']): h for h in history}

    def _format(opt):
        if opt == NEW_OPTION_KEY:
            return f"➕ 신규 작업  (오늘 / {next_seq}차 자동)"
        wd, seq = opt
        h = history_by_key.get((wd, seq))
        n = h['row_count'] if h else '?'
        wt = h.get('work_time') if h else None
        time_str = wt.strftime('%H:%M') if wt else ''
        src = (h.get('source_filename') if h else '') or ''
        src_short = (src[:30] + '…') if len(src) > 30 else src
        head = f"{wd.strftime('%Y-%m-%d')} / {seq}차"
        if time_str:
            head += f" - {time_str}"
        return f"{head} — {n}행" + (f" · {src_short}" if src_short else '')

    sel = st.selectbox(
        "작업일/차수 - 시간",
        options=options,
        format_func=_format,
        key=f"{key_prefix}_session_sel",
        help=(
            "신규 선택 시 작업일/차수 편집 가능. "
            "기존 선택 시 그 batch 덮어쓰기 모드 (저장 시 같은 키 갱신)."
        ),
    )

    if sel == NEW_OPTION_KEY:
        # 신규 = 오늘 / next_seq 자동. (수동 override UI 제거 — 드롭다운 라벨이
        # 이미 '오늘 / N차 자동' 으로 안내.)
        # 과거 작업 삭제는 expander 로 노출 (드롭다운 전환 없이 접근 가능).
        if history:
            _render_history_delete_expander(history, channel, key_prefix, adapter)
        return {
            'work_date': today,
            'sequence': int(next_seq),
            'is_new': True,
            'existing_meta': None,
        }
    else:
        wd, seq = sel
        meta = history_by_key.get((wd, seq))
        wt = meta.get('work_time') if meta else None
        time_part = f" - {wt.strftime('%H:%M')}" if wt else ''
        st.caption(
            f"📂 기존 작업 선택: **{wd.strftime('%Y-%m-%d')} / {seq}차{time_part}** "
            f"(현재 {meta['row_count']}행"
            + (f" · {meta['source_filename']}" if meta and meta.get('source_filename') else '')
            + ") — 저장 시 덮어쓰기."
        )
        _render_delete_action(channel, wd, int(seq), key_prefix,
                              suffix="existing", adapter=adapter)
        return {
            'work_date': wd,
            'sequence': int(seq),
            'is_new': False,
            'existing_meta': meta,
        }


def _render_history_delete_expander(history: List[Dict], channel: str,
                                     key_prefix: str,
                                     adapter: WorkSessionAdapter) -> None:
    """과거 작업 일괄 삭제 expander — 신규 모드에서 노출.

    각 row: 라벨 표시 + 삭제 체크박스. 체크박스 모두 체크 후 '🗑 선택 삭제' 버튼.
    """
    with st.expander(f"🗑 과거 작업 삭제 ({len(history)}건)", expanded=False):
        st.caption("체크 후 아래 '선택 삭제' 버튼 — DB에서 즉시 삭제됨 (복구 불가).")
        to_delete: List[tuple[datetime.date, int]] = []
        for h in history:
            wd, seq = h['work_date'], h['sequence']
            wt = h.get('work_time')
            time_str = wt.strftime('%H:%M') if wt else ''
            n = h['row_count']
            src = (h.get('source_filename') or '')
            src_short = (src[:30] + '…') if len(src) > 30 else src
            head = f"{wd.strftime('%Y-%m-%d')} / {seq}차"
            if time_str:
                head += f" - {time_str}"
            label = f"{head} — {n}행" + (f" · {src_short}" if src_short else '')
            chk_key = f"{key_prefix}_histdel_chk_{wd.isoformat()}_{seq}"
            if st.checkbox(label, key=chk_key, value=False):
                to_delete.append((wd, seq))
        btn_key = f"{key_prefix}_histdel_btn"
        if st.button(
            f"🗑 선택 삭제 ({len(to_delete)}건)",
            key=btn_key,
            disabled=(not to_delete),
            type="primary",
            width="stretch",
        ):
            ok_count = 0
            fail = []
            for wd, seq in to_delete:
                if adapter.delete_one(wd, seq, channel):
                    ok_count += 1
                    st.session_state.pop(
                        f"{key_prefix}_histdel_chk_{wd.isoformat()}_{seq}", None,
                    )
                else:
                    fail.append((wd, seq))
            if ok_count:
                st.success(f"✅ {ok_count}건 삭제됨.")
            if fail:
                st.error(
                    "삭제 실패: "
                    + ", ".join(f"{wd.strftime('%Y-%m-%d')}/{seq}차" for wd, seq in fail)
                )
            if ok_count:
                # selectbox state 도 초기화 (삭제된 row 선택 시 stale 방지)
                st.session_state.pop(f"{key_prefix}_session_sel", None)
                st.rerun()


def _render_delete_action(channel: str, work_date: datetime.date, sequence: int,
                          key_prefix: str, suffix: str,
                          adapter: WorkSessionAdapter) -> None:
    """기존 batch 삭제 — 2단계 확인 (체크박스 + 삭제 버튼)."""
    confirm_key = f"{key_prefix}_del_confirm_{suffix}"
    btn_key = f"{key_prefix}_del_btn_{suffix}"
    c_chk, c_btn = st.columns([3, 1])
    with c_chk:
        confirmed = st.checkbox(
            f"🗑 이 작업({work_date.strftime('%Y-%m-%d')} / {sequence}차) 삭제 확인",
            key=confirm_key, value=False,
            help="체크 후 오른쪽 버튼 클릭 시 DB 에서 즉시 삭제됨 (복구 불가).",
        )
    with c_btn:
        if st.button("🗑 삭제", key=btn_key, disabled=not confirmed,
                     width="stretch"):
            if adapter.delete_one(work_date, sequence, channel):
                st.success(
                    f"삭제됨 — {work_date.strftime('%Y-%m-%d')} / {sequence}차 / {channel}"
                )
                # 관련 위젯 state 초기화
                for k in (confirm_key, f"{key_prefix}_session_sel",
                          f"{key_prefix}_new_work_date", f"{key_prefix}_new_sequence"):
                    st.session_state.pop(k, None)
                st.rerun()
            else:
                st.error("삭제 실패 (DB 연결 확인)")


def is_session_blocked(session_info: Dict) -> bool:
    """세션 정보 → 저장/수집 차단 필요 여부.
    동일 (작업일, 차수) 가 이미 DB 에 있으면 차단. 사용자가 삭제 후 재등록해야 함.
    """
    if not session_info:
        return False
    # 기존 작업 선택 (is_new=False) 또는 신규에서 충돌 감지 (existing_meta truthy)
    return (not session_info.get('is_new')) or bool(session_info.get('existing_meta'))


def render_save_button(channel: str,
                       session_info: Dict,
                       daone_rows: List[Dict],
                       source_filename: str,
                       key_prefix: str) -> None:
    """저장 버튼 (다원 발주서 채널 전용 — daone_batch.upsert 호출)."""
    from db import daone_batch as _b
    if not daone_rows:
        st.button("💾 저장 (행 없음)", disabled=True, key=f"{key_prefix}_save_disabled")
        return
    if is_session_blocked(session_info):
        st.button(
            "💾 저장 — 같은 작업일/차수 이미 존재 (삭제 후 재등록)",
            disabled=True, width="stretch",
            key=f"{key_prefix}_save_blocked",
        )
        return
    label = f"💾 통합 발주서에 저장 ({len(daone_rows)}행)"
    if st.button(label, type="primary", width="stretch",
                 key=f"{key_prefix}_save_btn"):
        ok = _b.upsert(
            session_info['work_date'], session_info['sequence'], channel,
            daone_rows, source_filename=source_filename,
        )
        if ok:
            from channels import _db_cache as _cache
            _cache.invalidate_all()
            st.success(
                f"✅ 저장 완료 — {session_info['work_date'].strftime('%Y-%m-%d')} / "
                f"{session_info['sequence']}차 / {channel} ({len(daone_rows)}행)"
            )
            st.rerun()
        else:
            st.error("저장 실패 (DB 연결 확인)")
