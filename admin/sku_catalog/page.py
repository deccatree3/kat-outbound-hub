"""
KSE SKU 마스터 관리 페이지.

JP / KR location 별로 SKU 카탈로그(코드 + 상품명) CRUD.
출고 페이지의 매핑 등록 시 SKU 드롭다운의 source.
"""
import pandas as pd
import streamlit as st

from db import sku_catalog as sc


def _render_location_section(location: str, label: str):
    st.markdown(f"### {label} (`{location}`)")
    rows = sc.list_skus(location=location)

    c_search, c_filter = st.columns([2, 1])
    with c_search:
        search = st.text_input(
            "🔍 검색", key=f"sku_search_{location}",
            placeholder="SKU 코드 또는 상품명 일부 (공백시 전체)",
        )
    with c_filter:
        show_disabled = st.checkbox(
            "미사용 포함", value=False, key=f"sku_show_disabled_{location}",
        )

    filtered = rows
    if search:
        s = search.lower()
        filtered = [r for r in rows
                    if s in (r['sku_code'] or '').lower()
                    or s in (r['sku_name'] or '').lower()]
    if not show_disabled:
        filtered = [r for r in filtered if r['enabled']]

    df = pd.DataFrame([{
        'SKU 코드': r['sku_code'],
        '상품명': r['sku_name'] or '',
        '활성': '✅' if r['enabled'] else '⏸️',
        '비고': r['notes'] or '',
        '갱신': r['updated_at'].strftime('%Y-%m-%d %H:%M') if r['updated_at'] else '',
    } for r in filtered])

    st.caption(f"총 {len(rows)}개" + (f" · 필터 결과 {len(filtered)}개" if (search or not show_disabled) else ""))
    if df.empty:
        st.info("등록된 SKU가 없습니다." if not (search or not show_disabled) else "조건에 맞는 SKU가 없습니다.")
    else:
        st.dataframe(
            df, hide_index=True, width="stretch",
            column_config={
                'SKU 코드': st.column_config.TextColumn(width="medium"),
                '상품명': st.column_config.TextColumn(width="large"),
                '활성': st.column_config.TextColumn(width="small"),
                '비고': st.column_config.TextColumn(width="medium"),
                '갱신': st.column_config.TextColumn(width="small"),
            },
        )

    st.markdown(f"##### ✏️ {label} 편집")
    keys = [r['sku_code'] for r in rows]
    options = ['— 신규 등록 —'] + keys
    sel_idx = st.selectbox(
        "편집할 SKU", options=range(len(options)),
        format_func=lambda i: options[i],
        key=f"sku_sel_{location}",
    )

    if sel_idx == 0:
        ed_code = st.text_input("SKU 코드 *", value="", key=f"sku_code_new_{location}",
                                placeholder="예) KC_8809885876166")
        ed_name = st.text_input("상품명", value="", key=f"sku_name_new_{location}",
                                placeholder="예) NUKIT VOLCANO PEELING AMPOULE")
        ed_enabled = st.checkbox("활성", value=True, key=f"sku_en_new_{location}")
        ed_notes = st.text_input("비고", value="", key=f"sku_notes_new_{location}")
        is_new = True
        orig_code = None
    else:
        src = rows[sel_idx - 1]
        st.caption(f"**SKU 코드**: `{src['sku_code']}`")
        ed_code = src['sku_code']
        ed_name = st.text_input("상품명", value=src['sku_name'] or '',
                                key=f"sku_name_{location}_{sel_idx}")
        ed_enabled = st.checkbox("활성", value=bool(src['enabled']),
                                 key=f"sku_en_{location}_{sel_idx}")
        ed_notes = st.text_input("비고", value=src['notes'] or '',
                                 key=f"sku_notes_{location}_{sel_idx}")
        is_new = False
        orig_code = src['sku_code']

    btns = st.columns([1, 1, 4])
    with btns[0]:
        do_save = st.button(
            "➕ 추가" if is_new else "💾 저장",
            type="primary", width="stretch",
            key=f"sku_save_{location}_{sel_idx}",
        )
    with btns[1]:
        do_delete = False
        if not is_new:
            do_delete = st.button("🗑 삭제", width="stretch",
                                  key=f"sku_del_{location}_{sel_idx}")

    if do_save:
        code = (ed_code or '').strip()
        if not code:
            st.error("SKU 코드는 필수입니다.")
        else:
            ok = sc.upsert_sku(code, location, ed_name, ed_enabled, ed_notes)
            if ok:
                st.success("저장됨")
                st.rerun()
            else:
                st.error("저장 실패 (DB 연결 확인)")

    if do_delete and orig_code:
        ok = sc.delete_sku(orig_code, location)
        if ok:
            st.success("삭제됨")
            st.rerun()
        else:
            st.error("삭제 실패")


def render_page():
    sc.ensure_schema()
    counts = sc.count_by_location()
    st.markdown(
        "KSE 출고용 SKU 카탈로그를 관리합니다. "
        "**JP** = 일본 KSE 직접 출고용 SKU (자매 프로젝트와 공유). "
        "**KR** = 한국 KSE(다원) 출고용 SKU (큐텐 국내 등 다원 발주서)."
    )
    c1, c2 = st.columns(2)
    c1.metric("JP (일본 KSE) SKU 수", counts.get('JP', 0))
    c2.metric("KR (한국 KSE/다원) SKU 수", counts.get('KR', 0))

    tab_jp, tab_kr = st.tabs(["🇯🇵 JP — 일본 KSE", "🇰🇷 KR — 한국 KSE(다원)"])
    with tab_jp:
        _render_location_section('JP', '일본 KSE')
    with tab_kr:
        _render_location_section('KR', '한국 KSE(다원)')
