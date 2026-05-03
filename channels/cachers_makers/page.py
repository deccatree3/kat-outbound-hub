"""
캐처스 메이커스 (카카오메이커스) 출고 페이지.

입력:
  - 메이커스 주문내역.xlsx (시트명 '주문내역', 22컬럼)

매핑:
  - channel_product_mapping (channel='cachers_makers')
  - 매핑 없는 (상품, 옵션) 발견 시 어드민 → 🔧 상품 매핑 에서 등록 안내

출력:
  - 다원 발주서.xlsx (다원 표준 19컬럼)
"""
import datetime

import pandas as pd
import streamlit as st

from db import mapping as _map
from outputs.daone.builder import (
    parse_makers_xlsx,
    makers_to_daone_with_mapping,
    build_daone_xlsx,
)


CHANNEL_KEY = 'cachers_makers'


def _mapping_table():
    return pd.DataFrame([
        {'메이커스': '— 고정 —',          '다원 19컬럼': '몰명(또는 몰코드) = "000000000001"'},
        {'메이커스': '— 고정 —',          '다원 19컬럼': '출하의뢰번호 = "[캐처스] 카카오메이커스"'},
        {'메이커스': '배송번호',          '다원 19컬럼': '출하의뢰항번'},
        {'메이커스': '주문번호',          '다원 19컬럼': '고객주문번호'},
        {'메이커스': '상품 + 옵션',       '다원 19컬럼': '상품명'},
        {'메이커스': 'channel_product_mapping 조회 → SKU', '다원 19컬럼': '제품코드 (1→N 펼침)'},
        {'메이커스': '수량',              '다원 19컬럼': '주문수량 = SKU단위수량 × 메이커스수량'},
        {'메이커스': '수령인명',          '다원 19컬럼': '주문자명 / 수취인명'},
        {'메이커스': '수령인 연락처1',    '다원 19컬럼': '주문자연락처1 / 수취인연락처1'},
        {'메이커스': '수령인 연락처2',    '다원 19컬럼': '주문자연락처2 / 수취인연락처2'},
        {'메이커스': '우편번호',          '다원 19컬럼': '수취인우편번호'},
        {'메이커스': '배송주소',          '다원 19컬럼': '수취인주소1'},
        {'메이커스': '배송메시지',        '다원 19컬럼': '배송메시지'},
        {'메이커스': '송장번호',          '다원 19컬럼': '송장번호'},
        {'메이커스': '택배사명',          '다원 19컬럼': '택배사명'},
    ])


def _render_pending_warning(unknown_rows):
    """미매핑 (상품, 옵션) 발견 시 안내 — 등록은 어드민에서."""
    pending = {}
    for r in unknown_rows:
        key = (r['상품'], r['옵션'])
        pending.setdefault(key, r)

    if not pending:
        return

    st.error(
        f"🆕 **신규 매핑 등록 필요 {len(pending)}건** — "
        "사이드바 → ⚙️ 관리 → **🔧 상품 매핑** 에서 채널 = `cachers_makers` 선택 후 등록하세요. "
        "모두 해결되어야 다원 발주서 다운로드 가능."
    )

    summary = pd.DataFrame([
        {
            '상품': k[0],
            '옵션': k[1] or '(없음)',
            '대표 주문번호': sample.get('주문번호', ''),
            '대표 배송번호': sample.get('배송번호', ''),
        }
        for k, sample in pending.items()
    ])
    st.dataframe(
        summary, hide_index=True, width="stretch",
        column_config={
            '상품': st.column_config.TextColumn(width="large"),
            '옵션': st.column_config.TextColumn(width="medium"),
            '대표 주문번호': st.column_config.TextColumn(width="small"),
            '대표 배송번호': st.column_config.TextColumn(width="small"),
        },
    )


def render_page():
    _map.ensure_schema()
    st.markdown(
        "카카오메이커스 주문내역.xlsx → 다원 발주서.xlsx 변환. "
        "상품/옵션 ↔ SKU 매핑은 어드민에서 사전 등록."
    )

    uploaded = st.file_uploader(
        "메이커스 주문내역.xlsx",
        type=['xlsx'],
        key="makers_xlsx",
        help="이지오토 N — 메이커스에서 직접 다운로드한 주문내역 파일.",
    )

    if not uploaded:
        with st.expander("📋 메이커스 → 다원 19컬럼 매핑 (참고)", expanded=False):
            st.dataframe(_mapping_table(), hide_index=True, width="stretch")
        return

    try:
        makers_rows = parse_makers_xlsx(uploaded.getvalue())
    except Exception as ex:
        st.error(f"파싱 실패: {ex}")
        return

    if not makers_rows:
        st.warning("📭 메이커스 파일에 주문 데이터가 없습니다.")
        return

    try:
        mappings = _map.load_for_channel(CHANNEL_KEY)
    except Exception as ex:
        st.error(f"channel_product_mapping 로드 실패: {ex}")
        return

    result = makers_to_daone_with_mapping(makers_rows, mappings)
    daone_rows = result['daone_rows']
    unknown = result['unknown_rows']
    incomplete = result['incomplete_rows']

    today = datetime.date.today()
    c_d, c_s = st.columns([1, 1])
    work_date = c_d.date_input("작업일", value=today, key="makers_work_date")
    sequence = c_s.number_input(
        "차수", min_value=1, value=1, step=1, key="makers_sequence",
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("메이커스 행수", len(makers_rows))
    c2.metric("✅ 다원 행수 (펼침 후)", len(daone_rows))
    c3.metric("🆕 미매핑", len(unknown))
    c4.metric("⚠️ 미완전", len(incomplete),
              help="매핑은 있으나 sku_codes='-' (다원 SKU 미입력)")

    if incomplete:
        with st.expander(
            f"⚠️ incomplete 매핑 {len(incomplete)}건",
            expanded=False,
        ):
            st.dataframe(pd.DataFrame(incomplete), hide_index=True, width="stretch")

    _render_pending_warning(unknown)

    if unknown:
        return

    if not daone_rows:
        st.info("📭 다원 출고 대상 행이 없습니다.")
        return

    # 미리보기
    st.markdown("---")
    st.markdown("**미리보기**")
    df = pd.DataFrame(daone_rows)
    preview_cols = ['출하의뢰번호', '출하의뢰항번', '고객주문번호', '상품명', '제품코드',
                    '주문수량', '수취인명', '수취인우편번호', '수취인주소1', '송장번호', '택배사명']
    available = [c for c in preview_cols if c in df.columns]
    st.dataframe(df[available].head(50), width="stretch", hide_index=True)
    if len(df) > 50:
        st.caption(f"… 50/{len(df)} 행 표시")

    # 다운로드
    try:
        xlsx_bytes = build_daone_xlsx(daone_rows)
    except Exception as ex:
        st.error(f"다원 xlsx 생성 실패: {ex}")
        return

    unique_orders = len({r.get('고객주문번호', '') for r in daone_rows if r.get('고객주문번호')})
    total_qty = sum(int(r.get('주문수량', 0) or 0) for r in daone_rows)

    yymmdd = work_date.strftime('%y%m%d')
    out_name = f"{yymmdd}_{int(sequence)}차발주서_메이커스(주문건수 {unique_orders}, 주문량수 {total_qty}).xlsx"
    st.download_button(
        f"📥 {out_name}",
        data=xlsx_bytes,
        file_name=out_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", width="stretch",
        key="makers_daone_download",
    )
