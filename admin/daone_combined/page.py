"""
통합 다원 발주서 페이지 (어드민).

각 채널 페이지에서 '💾 저장' 한 batch 들의 평면 목록 → 자유롭게 체크박스 선택 →
선택한 batch 들 합산해서 통합 다원 발주서.xlsx 1개 다운로드.

채널별 작업일/차수가 달라도 자유 조합 가능. 잘못 저장된 batch 는 삭제.
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
        "각 채널 페이지에서 **💾 저장** 한 batch 들의 목록. "
        "포함할 batch 의 체크박스 선택 → 합산해서 통합 다원 발주서 1개 다운로드. "
        "**채널/작업일/차수 자유 조합**."
    )

    rows = _b.list_all(limit=200)
    if not rows:
        st.info("저장된 batch 가 아직 없습니다. 채널 페이지에서 '💾 저장' 누르면 여기 표시됩니다.")
        return

    # 필터
    c_ch, c_date_from, c_date_to = st.columns([2, 1, 1])
    with c_ch:
        all_channels = sorted({r['channel'] for r in rows})
        sel_channels = st.multiselect(
            "채널 필터 (비우면 전체)",
            options=all_channels,
            format_func=_channel_label,
            key="adm_combined_ch_filter",
        )
    with c_date_from:
        date_from = st.date_input(
            "작업일 ≥",
            value=None,
            key="adm_combined_date_from",
        )
    with c_date_to:
        date_to = st.date_input(
            "작업일 ≤",
            value=None,
            key="adm_combined_date_to",
        )

    filtered = rows
    if sel_channels:
        filtered = [r for r in filtered if r['channel'] in sel_channels]
    if date_from:
        filtered = [r for r in filtered if r['work_date'] >= date_from]
    if date_to:
        filtered = [r for r in filtered if r['work_date'] <= date_to]

    if not filtered:
        st.warning("필터 결과 없음.")
        return

    st.caption(f"전체 {len(rows)} batch · 필터 후 {len(filtered)} batch")

    # 체크박스 + 메타 표시 (data_editor)
    df = pd.DataFrame([{
        '포함': False,
        '채널':   _channel_label(r['channel']),
        '작업일': r['work_date'].strftime('%Y-%m-%d'),
        '차수':   f"{r['sequence']}차",
        '행수':   r['row_count'],
        '저장시각': r['updated_at'].strftime('%m-%d %H:%M') if r['updated_at'] else '',
        '원본': r.get('source_filename') or '',
        '_channel_key': r['channel'],
        '_work_date': r['work_date'],
        '_sequence': r['sequence'],
    } for r in filtered])

    edited = st.data_editor(
        df,
        column_config={
            '포함':      st.column_config.CheckboxColumn(width="small", default=False),
            '채널':      st.column_config.TextColumn(width="medium", disabled=True),
            '작업일':    st.column_config.TextColumn(width="small", disabled=True),
            '차수':      st.column_config.TextColumn(width="small", disabled=True),
            '행수':      st.column_config.NumberColumn(width="small", disabled=True),
            '저장시각':  st.column_config.TextColumn(width="small", disabled=True),
            '원본':      st.column_config.TextColumn(width="large", disabled=True),
            '_channel_key': None,  # hide
            '_work_date':   None,
            '_sequence':    None,
        },
        hide_index=True,
        width="stretch",
        key="adm_combined_editor",
    )

    selected = edited[edited['포함']]
    selected_count = len(selected)
    selected_rows_total = int(selected['행수'].sum()) if not selected.empty else 0

    st.markdown("---")
    c_dl, c_del = st.columns([3, 1])

    with c_dl:
        if selected_count == 0:
            st.button("📥 통합 발주서 다운로드 (선택 0건)",
                      disabled=True, width="stretch")
        else:
            # 선택된 batch 들의 row 풀 로드
            all_rows = []
            channel_summary = []
            for _, sel_row in selected.iterrows():
                ch = sel_row['_channel_key']
                wd = sel_row['_work_date']
                seq = sel_row['_sequence']
                b = _b.get(wd, seq, ch)
                if b:
                    all_rows.extend(b['rows'])
                    channel_summary.append(
                        f"{_channel_label(ch)} {wd.strftime('%m-%d')}/{seq}차 {b['row_count']}행"
                    )

            if not all_rows:
                st.error("선택한 batch 의 행을 불러오지 못했습니다.")
            else:
                try:
                    xlsx_bytes = build_daone_xlsx(all_rows)
                except Exception as ex:
                    st.error(f"통합 xlsx 생성 실패: {ex}")
                else:
                    today_str = datetime.date.today().strftime('%y%m%d')
                    unique_orders = len({r.get('주문번호', '') for r in all_rows if r.get('주문번호')})
                    total_qty = sum(int(r.get('주문수량', 0) or 0) for r in all_rows)
                    out_name = (
                        f"{today_str}_통합발주서"
                        f"({selected_count}건_주문 {unique_orders}_수량 {total_qty}).xlsx"
                    )
                    st.success(
                        f"포함: {' / '.join(channel_summary)} → 총 {len(all_rows)}행"
                    )
                    st.download_button(
                        f"📥 {out_name}",
                        data=xlsx_bytes,
                        file_name=out_name,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        type="primary", width="stretch",
                        key="adm_combined_download_btn",
                    )

    with c_del:
        if selected_count > 0:
            if st.button(f"🗑 선택 batch 삭제 ({selected_count})",
                         width="stretch", key="adm_combined_delete_btn"):
                deleted = 0
                for _, sel_row in selected.iterrows():
                    if _b.delete(sel_row['_work_date'], int(sel_row['_sequence']),
                                 sel_row['_channel_key']):
                        deleted += 1
                st.success(f"{deleted}건 삭제됨")
                st.rerun()

    # 선택된 batch 미리보기
    if selected_count > 0:
        with st.expander(f"📋 선택된 batch 합산 미리보기 ({selected_rows_total}행)",
                         expanded=False):
            preview_rows = []
            for _, sel_row in selected.iterrows():
                b = _b.get(sel_row['_work_date'], int(sel_row['_sequence']),
                           sel_row['_channel_key'])
                if b:
                    preview_rows.extend(b['rows'])
            if preview_rows:
                df_prev = pd.DataFrame(preview_rows)
                preview_cols = ['몰명(또는 몰코드)', '출하의뢰번호', '주문번호', '상품명',
                                '제품코드', '주문수량', '수취인명', '수취인우편번호', '수취인주소1']
                available = [c for c in preview_cols if c in df_prev.columns]
                st.dataframe(df_prev[available].head(100),
                             width="stretch", hide_index=True)
                if len(df_prev) > 100:
                    st.caption(f"… 100/{len(df_prev)} 행 표시")
