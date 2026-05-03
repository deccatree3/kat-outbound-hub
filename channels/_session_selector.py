"""
채널 페이지 공통 — 작업일/차수 selector.

드롭다운 옵션:
  ➕ 신규 작업 (today / next_seq 자동)
  ─ 기존 작업 ─
  YYYY-MM-DD / N차 — row_count 행 (source 파일명)
  ...

신규 선택 시: 작업일/차수 편집 가능 (default = today / next_seq)
기존 선택 시: 작업일/차수 read-only

반환: dict {'work_date', 'sequence', 'is_new', 'existing_meta'}
"""
import datetime
from typing import Dict, List

import streamlit as st

from db import daone_batch as _b


NEW_OPTION_KEY = '__new__'


def render_work_session_selector(channel: str, key_prefix: str) -> Dict:
    """채널 페이지 상단에 표시. 반환: {work_date, sequence, is_new, existing_meta}.

    key_prefix: 채널 페이지 안에서 위젯 key 충돌 방지용.
    """
    history = _b.list_keys_for_channel(channel, limit=50)
    next_seq = _b.next_sequence_for_channel(channel)
    today = datetime.date.today()

    options: List = [NEW_OPTION_KEY] + [(h['work_date'], h['sequence']) for h in history]
    history_by_key = {(h['work_date'], h['sequence']): h for h in history}

    def _format(opt):
        if opt == NEW_OPTION_KEY:
            return f"➕ 신규 작업  (오늘 / {next_seq}차 자동)"
        wd, seq = opt
        h = history_by_key.get((wd, seq))
        n = h['row_count'] if h else '?'
        src = (h.get('source_filename') if h else '') or ''
        src_short = (src[:30] + '…') if len(src) > 30 else src
        return f"{wd.strftime('%Y-%m-%d')} / {seq}차 — {n}행" + (f" · {src_short}" if src_short else '')

    sel = st.selectbox(
        "작업일/차수",
        options=options,
        format_func=_format,
        key=f"{key_prefix}_session_sel",
        help=(
            "신규 선택 시 작업일/차수 편집 가능. "
            "기존 선택 시 그 batch 덮어쓰기 모드 (저장 시 같은 키 갱신)."
        ),
    )

    if sel == NEW_OPTION_KEY:
        c_d, c_s = st.columns([1, 1])
        work_date = c_d.date_input(
            "작업일", value=today, key=f"{key_prefix}_new_work_date",
        )
        sequence = c_s.number_input(
            "차수", min_value=1, value=int(next_seq), step=1,
            key=f"{key_prefix}_new_sequence",
        )
        return {
            'work_date': work_date,
            'sequence': int(sequence),
            'is_new': True,
            'existing_meta': None,
        }
    else:
        wd, seq = sel
        meta = history_by_key.get((wd, seq))
        st.caption(
            f"📂 기존 작업 선택: **{wd.strftime('%Y-%m-%d')} / {seq}차** "
            f"(현재 {meta['row_count']}행"
            + (f" · {meta['source_filename']}" if meta and meta.get('source_filename') else '')
            + ") — 저장 시 덮어쓰기."
        )
        return {
            'work_date': wd,
            'sequence': int(seq),
            'is_new': False,
            'existing_meta': meta,
        }


def render_save_button(channel: str,
                       session_info: Dict,
                       daone_rows: List[Dict],
                       source_filename: str,
                       key_prefix: str) -> None:
    """저장 버튼. 클릭 시 daone_batch.upsert."""
    if not daone_rows:
        st.button("💾 저장 (행 없음)", disabled=True, key=f"{key_prefix}_save_disabled")
        return
    label = f"💾 통합 발주서에 저장 ({len(daone_rows)}행)"
    if not session_info['is_new']:
        label = f"💾 덮어쓰기 저장 ({len(daone_rows)}행)"
    if st.button(label, type="secondary", width="stretch",
                 key=f"{key_prefix}_save_btn"):
        ok = _b.upsert(
            session_info['work_date'], session_info['sequence'], channel,
            daone_rows, source_filename=source_filename,
        )
        if ok:
            st.success(
                f"✅ 저장 완료 — {session_info['work_date'].strftime('%Y-%m-%d')} / "
                f"{session_info['sequence']}차 / {channel} ({len(daone_rows)}행)"
            )
            st.rerun()
        else:
            st.error("저장 실패 (DB 연결 확인)")
