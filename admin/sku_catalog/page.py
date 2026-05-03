"""
SKU 카탈로그 관리 페이지.

모든 채널이 공유하는 SKU 마스터 (sku_code, sku_name, notes).
출고 페이지의 매핑 등록 시 SKU 드롭다운의 source.
"""
import pandas as pd
import streamlit as st

from db import sku_catalog as sc


def render_page():
    sc.ensure_schema()
    total = sc.total_count()
    st.markdown(
        "모든 채널이 공유하는 **SKU 마스터**. "
        "매핑 등록 시 드롭다운 source 로 사용됩니다."
    )
    st.metric("총 SKU 수", total)

    rows = sc.list_skus()

    c_search, _ = st.columns([2, 1])
    with c_search:
        search = st.text_input(
            "🔍 검색", key="sku_search",
            placeholder="SKU 코드 또는 상품명 일부 (공백시 전체)",
        )

    filtered = rows
    if search:
        s = search.lower()
        filtered = [r for r in rows
                    if s in (r['sku_code'] or '').lower()
                    or s in (r['sku_name'] or '').lower()]

    df = pd.DataFrame([{
        'SKU 코드': r['sku_code'],
        '상품명': r['sku_name'] or '',
        '비고': r['notes'] or '',
        '갱신': r['updated_at'].strftime('%Y-%m-%d %H:%M') if r['updated_at'] else '',
    } for r in filtered])

    st.caption(f"총 {len(rows)}개" + (f" · 필터 결과 {len(filtered)}개" if search else ""))
    if df.empty:
        st.info("등록된 SKU가 없습니다." if not search else "조건에 맞는 SKU가 없습니다.")
    else:
        st.dataframe(
            df, hide_index=True, width="stretch",
            column_config={
                'SKU 코드': st.column_config.TextColumn(width="medium"),
                '상품명': st.column_config.TextColumn(width="large"),
                '비고': st.column_config.TextColumn(width="medium"),
                '갱신': st.column_config.TextColumn(width="small"),
            },
        )

    st.markdown("##### ✏️ 편집")
    keys = [r['sku_code'] for r in rows]
    options = ['— 신규 등록 —'] + keys
    sel_idx = st.selectbox(
        "편집할 SKU", options=range(len(options)),
        format_func=lambda i: options[i],
        key="sku_sel",
    )

    if sel_idx == 0:
        ed_code = st.text_input("SKU 코드 *", value="", key="sku_code_new",
                                placeholder="예) KC_8809885876166")
        ed_name = st.text_input("상품명", value="", key="sku_name_new",
                                placeholder="예) NUKIT VOLCANO PEELING AMPOULE")
        ed_notes = st.text_input("비고", value="", key="sku_notes_new")
        is_new = True
        orig_code = None
    else:
        src = rows[sel_idx - 1]
        st.caption(f"**SKU 코드**: `{src['sku_code']}`")
        ed_code = src['sku_code']
        ed_name = st.text_input("상품명", value=src['sku_name'] or '',
                                key=f"sku_name_{sel_idx}")
        ed_notes = st.text_input("비고", value=src['notes'] or '',
                                 key=f"sku_notes_{sel_idx}")
        is_new = False
        orig_code = src['sku_code']

    btns = st.columns([1, 1, 4])
    with btns[0]:
        do_save = st.button(
            "➕ 추가" if is_new else "💾 저장",
            type="primary", width="stretch",
            key=f"sku_save_{sel_idx}",
        )
    with btns[1]:
        do_delete = False
        if not is_new:
            do_delete = st.button("🗑 삭제", width="stretch",
                                  key=f"sku_del_{sel_idx}")

    if do_save:
        code = (ed_code or '').strip()
        if not code:
            st.error("SKU 코드는 필수입니다.")
        else:
            ok = sc.upsert_sku(code, sku_name=ed_name, notes=ed_notes)
            if ok:
                st.success("저장됨")
                st.rerun()
            else:
                st.error("저장 실패 (DB 연결 확인)")

    if do_delete and orig_code:
        ok = sc.delete_sku(orig_code)
        if ok:
            st.success("삭제됨")
            st.rerun()
        else:
            st.error("삭제 실패")
