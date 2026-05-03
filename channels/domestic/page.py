"""
국내몰 Streamlit 페이지.

EZA 확장주문검색.xls 한 번 업로드 → 두 출력 동시 제공:
  - 다원 발주서.xlsx — 판매처그룹='캐처스' 행만 변환 (다원 수기 등록용)
  - 일반주문 번들작업건.xlsx — 판매처그룹≠'캐처스'(네뉴 등) 세트 주문만 양식 채움

EZA ↔ 다원 자동 연동은 네뉴만 활성. 캐처스는 다원 수기 업로드.
"""
import datetime

import pandas as pd
import streamlit as st

from outputs.daone.builder import (
    parse_eza_xls,
    transform_to_daone,
    build_daone_xlsx,
)
from outputs.nenu_bundle.builder import build_bundle_xlsx


def _eza_mapping_table():
    return pd.DataFrame([
        {'EZA 컬럼': '판매처그룹',           '다원 컬럼': '— drop (제품코드 분기 + 출력 필터 조건) —'},
        {'EZA 컬럼': '몰명(또는 몰코드)',    '다원 컬럼': '몰명(또는 몰코드) — 빈값이면 "000000000001"'},
        {'EZA 컬럼': '출하의뢰번호',         '다원 컬럼': '출하의뢰번호'},
        {'EZA 컬럼': '출하의뢰항번',         '다원 컬럼': '출하의뢰항번'},
        {'EZA 컬럼': '주문번호',             '다원 컬럼': '고객주문번호'},
        {'EZA 컬럼': '상품명',               '다원 컬럼': '상품명'},
        {'EZA 컬럼': '제품코드',             '다원 컬럼': '제품코드 — 빈값이면 판매처그룹="캐처스"→상품메모, 그 외→바코드'},
        {'EZA 컬럼': '바코드',               '다원 컬럼': '— 제품코드 fallback —'},
        {'EZA 컬럼': '상품메모',             '다원 컬럼': '— 캐처스 제품코드 fallback —'},
        {'EZA 컬럼': '상품수량',             '다원 컬럼': '주문수량'},
        {'EZA 컬럼': '주문자이름',           '다원 컬럼': '주문자명'},
        {'EZA 컬럼': '주문자연락처1',         '다원 컬럼': '주문자연락처1'},
        {'EZA 컬럼': '주문자연락처2',         '다원 컬럼': '주문자연락처2'},
        {'EZA 컬럼': '수취인명',             '다원 컬럼': '수취인명'},
        {'EZA 컬럼': '수취인연락처1',         '다원 컬럼': '수취인연락처1'},
        {'EZA 컬럼': '수취인연락처2',         '다원 컬럼': '수취인연락처2'},
        {'EZA 컬럼': '수취인우편번호',       '다원 컬럼': '수취인우편번호'},
        {'EZA 컬럼': '수취인주소1',           '다원 컬럼': '수취인주소1'},
        {'EZA 컬럼': '주소2',                '다원 컬럼': '주소2 — 빈값이면 수취인주소1 복사'},
        {'EZA 컬럼': '배송메시지',           '다원 컬럼': '배송메시지'},
        {'EZA 컬럼': '송장번호',             '다원 컬럼': '송장번호'},
        {'EZA 컬럼': '택배사명',             '다원 컬럼': '택배사명'},
    ])


def _render_metrics_and_preview(daone_rows):
    c1, c2, c3 = st.columns(3)
    c1.metric("주문 행수", len(daone_rows))
    unique_orders = len({r.get('고객주문번호', '') for r in daone_rows if r.get('고객주문번호')})
    c2.metric("주문번호 (고유)", unique_orders)
    try:
        total_qty = sum(int(r.get('주문수량', 0) or 0) for r in daone_rows)
    except Exception:
        total_qty = 0
    c3.metric("주문수량 합계", total_qty)

    df_preview = pd.DataFrame(daone_rows)
    st.markdown("**미리보기** (다원 양식)")
    preview_cols = ['몰명(또는 몰코드)', '고객주문번호', '상품명', '제품코드', '주문수량',
                    '수취인명', '수취인우편번호', '수취인주소1', '배송메시지']
    available = [c for c in preview_cols if c in df_preview.columns]
    st.dataframe(df_preview[available].head(50), width="stretch", hide_index=True)
    if len(df_preview) > 50:
        st.caption(f"… 50/{len(df_preview)} 행 표시")
    return unique_orders, total_qty


def _section_daone(eza_rows, work_date, sequence):
    st.markdown("### 📋 다원 발주서 (캐처스만)")
    st.caption("판매처그룹='캐처스' 행만 변환. 나머지(네뉴 등) 행은 자동 제외.")

    cachers_rows = [r for r in eza_rows if str(r.get('판매처그룹', '')).strip() == '캐처스']
    if not cachers_rows:
        st.info("📭 캐처스 행이 없어 다원 발주서를 생성하지 않습니다.")
        return

    daone_rows = transform_to_daone(cachers_rows)
    unique_orders, total_qty = _render_metrics_and_preview(daone_rows)

    try:
        xlsx_bytes = build_daone_xlsx(daone_rows)
    except Exception as ex:
        st.error(f"다원 xlsx 생성 실패: {ex}")
        return

    yymmdd = work_date.strftime('%y%m%d')
    out_name = f"{yymmdd}_{int(sequence)}차발주서(주문건수 {unique_orders}, 주문량수 {total_qty}).xlsx"
    st.download_button(
        f"📥 {out_name}",
        data=xlsx_bytes,
        file_name=out_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", width="stretch",
        key="daone_download",
    )
    st.caption("📤 다원 WMS에 수기 업로드.")


def _section_bundle(eza_bytes, work_date, sequence):
    st.markdown("### 📦 일반주문 번들작업건 (네뉴 세트만)")
    st.caption(
        "EZA에서 판매처그룹='캐처스' 행은 자동 제외. 마스터 양식의 세트 행 D셀에 EZA 합계 정수 입력. "
        "단품 출고수량(C)은 Excel SUMIFS로 자동 계산."
    )

    try:
        xlsx_bytes, info = build_bundle_xlsx(eza_bytes, work_date, int(sequence))
    except Exception as ex:
        st.error(f"번들작업파일 생성 실패: {ex}")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("세트 매칭/채움", f"{len(info['set_matched_barcodes'])} / {info['set_rows_filled']}")
    c2.metric("총 세트 입고수량", info['total_qty'])
    c3.metric("단품 직접 주문 (참고)", len(info['single_matched_barcodes']))
    c4.metric("EZA(네뉴) 종/수량", f"{info['eza_total_rows']} / {info['eza_total_qty']}")
    st.caption(f"마스터 = 단품 {info['master_single_count']}개 + 세트 {info['master_set_count']}개.")

    if info['unmatched_barcodes']:
        st.warning(
            f"⚠️ EZA(네뉴)에 있으나 마스터에 없는 바코드 **{len(info['unmatched_barcodes'])}건** — "
            "신규 SKU 또는 마스터 누락 가능성. `outputs/nenu_bundle/template.xlsx` 검토 필요."
        )
        with st.expander("미매칭 바코드 목록", expanded=False):
            st.code('\n'.join(info['unmatched_barcodes']))

    if info['single_matched_barcodes']:
        with st.expander(
            f"📦 단품 직접 주문 {len(info['single_matched_barcodes'])}건 "
            "(양식에 자리 없음 — EZA↔다원 자동 흐름이 처리)",
            expanded=False,
        ):
            st.code('\n'.join(info['single_matched_barcodes']))

    out_name = f"일반주문 번들작업건_{work_date.strftime('%y%m%d')}_{int(sequence)}차.xlsx"
    st.download_button(
        f"📥 {out_name}",
        data=xlsx_bytes,
        file_name=out_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", width="stretch",
        key=f"nenu_bundle_download_{work_date}_{sequence}",
    )


def render_page():
    st.markdown(
        "EZA **확장주문검색.xls** 한 번 업로드 → **다원 발주서**(캐처스) + **번들작업파일**(네뉴 세트) 동시 생성. "
        "이지오토 Y 흐름이라 EZA가 자동 수집, 우리는 변환만."
    )

    uploaded_files = st.file_uploader(
        "EZA 확장주문검색 파일 (.xls, 여러 개 가능)",
        type=['xls'],
        accept_multiple_files=True,
        key="domestic_eza",
        help="EZA > 주문관리 > 확장주문검색 > 엑셀다운. 여러 개 한꺼번에 끌어다 놓을 수 있음.",
    )

    if not uploaded_files:
        with st.expander("📋 EZA → 다원 19컬럼 매핑 (참고)", expanded=False):
            st.dataframe(_eza_mapping_table(), hide_index=True, width="stretch")
        return

    eza_rows = []
    parse_errors = []
    for f in uploaded_files:
        try:
            rows = parse_eza_xls(f.getvalue())
            eza_rows.extend(rows)
        except Exception as ex:
            parse_errors.append(f"{f.name}: {ex}")
    if parse_errors:
        st.error("일부 파일 파싱 실패:\n" + "\n".join(parse_errors))
    if len(uploaded_files) > 1:
        st.caption(f"📂 {len(uploaded_files)}개 파일 합산 — 총 {len(eza_rows)} 행")

    if not eza_rows:
        st.warning("📭 EZA 파일에 주문 데이터가 없습니다.")
        return

    today = datetime.date.today()
    c_d, c_s = st.columns([1, 1])
    work_date = c_d.date_input("작업일", value=today, key="domestic_work_date")
    sequence = c_s.number_input(
        "차수", min_value=1, value=1, step=1, key="domestic_sequence",
        help="같은 날 재실행 시 직접 +1 변경.",
    )

    _section_daone(eza_rows, work_date, int(sequence))
    st.markdown("---")
    _section_bundle(eza_bytes, work_date, int(sequence))
