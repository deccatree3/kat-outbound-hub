"""
통합 다원 발주서 페이지 (어드민).

채널 페이지에서 "💾 저장"으로 저장된 batch 들을 작업일/차수 단위로 모아 보고,
원하는 채널만 선택해서 통합 다원 발주서 1개 xlsx 다운로드.
"""
import datetime

import pandas as pd
import streamlit as st

from db import daone_batch as _b
from outputs.daone.builder import build_daone_xlsx


CHANNEL_LABELS = {
    'domestic':         '국내몰 (캐처스)',
    'cachers_qoo10_kr': '캐처스 큐텐 국내 (KSE)',
    'cachers_makers':   '캐처스 메이커스',
}


def _channel_label(c: str) -> str:
    return CHANNEL_LABELS.get(c, c)


def render_page():
    _b.ensure_schema()

    st.markdown(
        "각 채널 페이지에서 **💾 저장** 한 batch 를 작업일/차수 단위로 모아 통합 발주서 1개로 다운로드. "
        "체크박스로 채널 선택, 잘못 저장된 batch 는 삭제 가능."
    )

    sessions = _b.list_all_sessions(limit=50)
    if not sessions:
        st.info("저장된 batch 가 아직 없습니다. 채널 페이지에서 '💾 저장' 누르면 여기 표시됩니다.")
        return

    sel = st.selectbox(
        "작업일 / 차수",
        options=sessions,
        format_func=lambda s: f"{s[0].strftime('%Y-%m-%d')} / {s[1]}차",
        key="adm_combined_sel",
    )

    work_date, sequence = sel
    metas = _b.list_for_session(work_date, sequence)
    if not metas:
        st.warning("선택한 작업일/차수에 batch 가 없습니다.")
        return

    total_rows = sum(m['row_count'] for m in metas)
    st.markdown(
        f"### 📦 {work_date.strftime('%Y-%m-%d')} / {sequence}차 — {len(metas)} 채널 / {total_rows} 행"
    )

    # 채널 카드 + 선택
    selected_channels = []
    for m in metas:
        ch = m['channel']
        with st.container(border=True):
            c_chk, c_info, c_del = st.columns([1, 6, 1])
            with c_chk:
                checked = st.checkbox(
                    "포함",
                    value=True,
                    key=f"adm_chk_{ch}_{work_date}_{sequence}",
                    label_visibility='collapsed',
                )
            with c_info:
                st.markdown(
                    f"**{_channel_label(ch)}** "
                    f"<span style='color:#888;font-size:0.85em'>({ch})</span>",
                    unsafe_allow_html=True,
                )
                meta_line = (
                    f"📊 {m['row_count']} 행 · "
                    f"💾 {m['updated_at'].strftime('%H:%M')} 저장"
                )
                if m.get('source_filename'):
                    meta_line += f" · 📂 `{m['source_filename']}`"
                st.caption(meta_line)
            with c_del:
                if st.button("🗑", key=f"adm_del_{ch}_{work_date}_{sequence}",
                             help="이 batch 삭제"):
                    if _b.delete(work_date, sequence, ch):
                        st.success(f"{_channel_label(ch)} batch 삭제됨")
                        st.rerun()
                    else:
                        st.error("삭제 실패")
        if checked:
            selected_channels.append(ch)

    st.markdown("---")

    if not selected_channels:
        st.info("📭 포함할 채널이 없습니다 (모두 체크 해제).")
        return

    # 선택된 채널 batch 풀 로드 + 통합
    all_rows = []
    channel_summary = []
    for ch in selected_channels:
        b = _b.get(work_date, sequence, ch)
        if b:
            all_rows.extend(b['rows'])
            channel_summary.append(f"{_channel_label(ch)} {b['row_count']}")

    if not all_rows:
        st.warning("선택된 채널의 행을 불러오지 못했습니다.")
        return

    st.markdown("**미리보기 (통합)**")
    df = pd.DataFrame(all_rows)
    preview_cols = ['몰명(또는 몰코드)', '출하의뢰번호', '주문번호', '상품명', '제품코드',
                    '주문수량', '수취인명', '수취인우편번호', '수취인주소1', '송장번호', '택배사명']
    available = [c for c in preview_cols if c in df.columns]
    st.dataframe(df[available].head(100), width="stretch", hide_index=True)
    if len(df) > 100:
        st.caption(f"… 100/{len(df)} 행 표시")

    # 통합 다원 xlsx
    try:
        xlsx_bytes = build_daone_xlsx(all_rows)
    except Exception as ex:
        st.error(f"통합 xlsx 생성 실패: {ex}")
        return

    yymmdd = work_date.strftime('%y%m%d')
    unique_orders = len({r.get('주문번호', '') for r in all_rows if r.get('주문번호')})
    total_qty = sum(int(r.get('주문수량', 0) or 0) for r in all_rows)
    out_name = (
        f"{yymmdd}_{int(sequence)}차_통합발주서"
        f"(주문건수 {unique_orders}, 주문량수 {total_qty}).xlsx"
    )

    st.success(f"포함 채널: {', '.join(channel_summary)} → 총 {len(all_rows)} 행")
    st.download_button(
        f"📥 {out_name}",
        data=xlsx_bytes,
        file_name=out_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", width="stretch",
        key=f"adm_combined_dl_{work_date}_{sequence}",
    )
    st.caption("📤 다원 WMS에 1번에 업로드.")
