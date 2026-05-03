"""
상품 매핑 통합 관리 페이지 (어드민).

모든 채널의 channel_product_mapping CRUD. 채널 필터 + 검색 + 편집/삭제.
KSE OMS 파일 업로드 없이도 잘못 등록된 매핑 수정/삭제 가능.
"""
import pandas as pd
import streamlit as st

from db import mapping as _map


CHANNEL_LABELS = {
    'qoo10_japan':       'Qoo10 일본 (캐처스)',
    'cachers_qoo10_kr':  '캐처스 큐텐 국내 (KSE)',
    'cachers_makers':    '캐처스 메이커스',
}


def _channel_label(ch: str) -> str:
    return CHANNEL_LABELS.get(ch, ch)


def _summary_cell(item_codes: str, quantities: str) -> str:
    names = [n.strip() for n in (item_codes or '').split(',') if n.strip()]
    qtys = [q.strip() for q in (quantities or '').split(',') if q.strip()]
    if len(qtys) < len(names):
        qtys += ['1'] * (len(names) - len(qtys))
    return ' + '.join(f"{n}×{q}" for n, q in zip(names, qtys))


def render_page():
    _map.ensure_schema()
    counts = _map.count_by_channel()

    st.markdown(
        "모든 채널의 **상품/옵션 ↔ SKU 매핑** 을 한 곳에서 관리합니다. "
        "잘못 등록된 매핑 수정/삭제도 여기서."
    )

    # 채널별 metric
    if counts:
        cols = st.columns(max(len(counts), 1))
        for i, (ch, n) in enumerate(sorted(counts.items())):
            cols[i].metric(_channel_label(ch), n)
    else:
        st.info("등록된 매핑이 아직 없습니다.")

    # 필터
    c_ch, c_search = st.columns([1, 2])
    with c_ch:
        ch_options = ['(전체)'] + sorted(counts.keys()) if counts else ['(전체)']
        sel_ch = st.selectbox(
            "채널 필터", options=ch_options,
            format_func=lambda x: '전체' if x == '(전체)' else _channel_label(x),
        )
    with c_search:
        search = st.text_input(
            "🔍 검색 (상품명/옵션/SKU 코드/상품명 일부)",
            placeholder="공백 시 전체",
        )

    chan = None if sel_ch == '(전체)' else sel_ch
    rows = _map.list_all(channel=chan, search=search)

    # 요약 테이블
    summary = pd.DataFrame([{
        '채널': _channel_label(r['channel']),
        '상품명': r['product_name'],
        '옵션': r['product_option'] or '',
        'SKU 구성': _summary_cell(r['item_codes'], r['quantities']),
        '품목수': len([s for s in r['sku_codes'].split(',') if s.strip()]),
        '갱신': r['updated_at'].strftime('%Y-%m-%d %H:%M') if r['updated_at'] else '',
    } for r in rows])

    st.caption(f"총 {len(rows)} 건")
    if not summary.empty:
        st.dataframe(
            summary, hide_index=True, width="stretch",
            column_config={
                '채널':     st.column_config.TextColumn(width="medium"),
                '상품명':   st.column_config.TextColumn(width="large"),
                '옵션':     st.column_config.TextColumn(width="medium"),
                'SKU 구성': st.column_config.TextColumn(width="large"),
                '품목수':   st.column_config.NumberColumn(width="small"),
                '갱신':     st.column_config.TextColumn(width="small"),
            },
        )

    st.markdown("---")
    st.markdown("### ✏️ 매핑 편집")

    # 편집 selectbox
    keys = [(r['channel'], r['product_name'], r['product_option']) for r in rows]
    options = ['— 새 매핑 추가 —'] + [
        f"[{_channel_label(c)}] {n[:40]}{'...' if len(n)>40 else ''} / {(o or '(옵션없음)')[:30]}"
        for c, n, o in keys
    ]
    sel_idx = st.selectbox(
        "편집할 매핑 선택", options=range(len(options)),
        format_func=lambda i: options[i], key="adm_map_sel",
    )

    if sel_idx == 0:
        # 신규 추가
        c_ch2, _ = st.columns([1, 2])
        all_chans = sorted(set(list(counts.keys()) + list(CHANNEL_LABELS.keys())))
        new_ch = c_ch2.selectbox(
            "채널", options=all_chans,
            format_func=_channel_label, key="adm_map_new_ch",
        )
        edit_ch = new_ch
        edit_pn = st.text_area("상품명", value="", height=80, key="adm_map_new_pn")
        edit_po = st.text_input("옵션 (없으면 빈칸)", value="", key="adm_map_new_po")
        init_sku_df = pd.DataFrame({
            'SKU 코드': [''], '상품명': [''], '수량': [1],
        })
        is_new = True
        orig_key = None
    else:
        ch_orig, pn_orig, po_orig = keys[sel_idx - 1]
        src_row = rows[sel_idx - 1]
        st.markdown(f"**채널**: `{_channel_label(ch_orig)}` ({ch_orig})")
        st.markdown(f"**상품명**: `{pn_orig}`")
        st.markdown(f"**옵션**: `{po_orig or '(없음)'}`")
        edit_ch = ch_orig
        edit_pn = pn_orig
        edit_po = po_orig
        names = [n.strip() for n in (src_row['item_codes'] or '').split(',')]
        codes = [c.strip() for c in (src_row['sku_codes'] or '').split(',')]
        qtys  = [int(q) for q in (src_row['quantities'] or '').split(',') if q.strip()]
        max_n = max(len(names), len(codes), len(qtys), 1)
        names += [''] * (max_n - len(names))
        codes += [''] * (max_n - len(codes))
        qtys  += [1]  * (max_n - len(qtys))
        init_sku_df = pd.DataFrame({
            'SKU 코드': codes,
            '상품명':   names,
            '수량':     qtys,
        })
        is_new = False
        orig_key = (ch_orig, pn_orig, po_orig)

    st.markdown("**SKU 구성** (세트면 행 추가)")
    sku_edited = st.data_editor(
        init_sku_df,
        column_config={
            'SKU 코드': st.column_config.TextColumn(required=True, width="medium",
                       help="예) KC_8809885876166"),
            '상품명':   st.column_config.TextColumn(width="large",
                       help="비고용 — 빈값이면 SKU 코드로 채워짐"),
            '수량':     st.column_config.NumberColumn(min_value=1, step=1, default=1,
                       required=True, width="small"),
        },
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        key=f"adm_map_editor_{sel_idx}",
    )

    btns = st.columns([1, 1, 4])
    with btns[0]:
        do_save = st.button(
            "➕ 추가" if is_new else "💾 저장",
            type="primary", width="stretch", key=f"adm_map_save_{sel_idx}",
        )
    with btns[1]:
        do_delete = False
        if not is_new:
            do_delete = st.button(
                "🗑 삭제", width="stretch", key=f"adm_map_del_{sel_idx}",
            )

    if do_save:
        pn = str(edit_pn or '').strip()
        po = str(edit_po or '').strip()
        if not pn:
            st.error("상품명은 필수입니다.")
        else:
            valid = sku_edited[sku_edited['SKU 코드'].astype(str).str.strip() != '']
            if valid.empty:
                st.error("최소 1개 SKU 코드 필요.")
            else:
                payload = []
                for _, r in valid.iterrows():
                    code = str(r['SKU 코드']).strip()
                    name = str(r['상품명'] or '').strip() or code
                    qty = int(r['수량'] or 1)
                    payload.append((code, name, qty))
                # 키가 바뀐 수정이면 기존 행 삭제 후 신규 등록
                if orig_key and (edit_ch, pn, po) != orig_key:
                    _map.delete(*orig_key)
                if _map.upsert(edit_ch, pn, po, payload):
                    st.success("저장됨")
                    st.rerun()
                else:
                    st.error("저장 실패 (DB 연결 확인)")

    if do_delete and orig_key:
        if _map.delete(*orig_key):
            st.success("삭제됨")
            st.rerun()
        else:
            st.error("삭제 실패")
