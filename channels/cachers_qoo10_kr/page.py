"""
캐처스 큐텐 국내출고 Streamlit 페이지.

Qoo10 일본 주문 중 한국 다원 → KSE 한국 → 일본 흐름.

입력:
  - KSE OMS 주문내역.xlsx (필수)
  - KSE 쉽먼트 라벨.pdf (선택, 인박스 부착)

매핑 흐름 (channel_product_mapping, channel='cachers_qoo10_kr'):
  매핑 없음           → 신규 등록 모달 (다원 SKU 구성 + 카탈로그 드롭다운)
  매핑 있음           → sku_codes 펼쳐서 1→N 다원 행
  매핑 sku_codes='-' → 다원 SKU 미입력 — 매핑 갱신 필요 (incomplete)

출력:
  - 다원 발주서.xlsx (다원 표준 19컬럼)
  - KSE 부착문서 PDF (아웃박스용)
  - 쉽먼트 라벨 PDF (사용자 업로드 → 파일명 정리 후 재다운)
"""
import datetime

import pandas as pd
import streamlit as st

from db import mapping as _map
from outputs.daone.builder import (
    parse_kse_oms_xlsx,
    kse_oms_to_daone_with_mapping,
    build_daone_xlsx,
)
from outputs.kse_label.attached import build_kse_attached_pdf
from outputs.packing.boxes import compute_packing
from channels._session_selector import (
    render_work_session_selector, render_save_button,
)


CHANNEL_KEY = 'cachers_qoo10_kr'


def _kse_mapping_table():
    return pd.DataFrame([
        {'KSE OMS': '판매마켓',                    '다원 19컬럼': '출하의뢰번호'},
        {'KSE OMS': '접수번호',                     '다원 19컬럼': '출하의뢰항번'},
        {'KSE OMS': '주문번호',                     '다원 19컬럼': '주문번호'},
        {'KSE OMS': '상품명 + 옵션명',              '다원 19컬럼': '상품명'},
        {'KSE OMS': 'channel_product_mapping 조회 → SKU', '다원 19컬럼': '제품코드 (1→N 펼침)'},
        {'KSE OMS': '수량',                         '다원 19컬럼': '주문수량 = SKU단위수량 × KSE수량'},
        {'KSE OMS': '받는사람',                     '다원 19컬럼': '주문자명 / 수취인명'},
        {'KSE OMS': '받는사람전화',                  '다원 19컬럼': '주문자연락처1 / 수취인연락처1'},
        {'KSE OMS': '우편번호',                     '다원 19컬럼': '수취인우편번호'},
        {'KSE OMS': '주소',                         '다원 19컬럼': '수취인주소1 = 주소2'},
        {'KSE OMS': '도착지송장번호',                '다원 19컬럼': '송장번호'},
        {'KSE OMS': '배송타입 (KSE)',               '다원 19컬럼': '택배사명'},
        {'KSE OMS': '— 고정 —',                     '다원 19컬럼': '몰명(또는 몰코드) = "000000000001"'},
    ])


def _render_pending_mappings(unknown_rows, mappings):
    """KSE 파일에 등장한 신규 (상품명, 옵션) — 매핑 등록 모달.
    SKU 코드 + 이름 + 수량 직접 입력. 기존 매핑의 SKU는 참고용으로 expander 표시.
    """
    known_skus = _map.list_known_skus()

    # 키 단위로 합치기 (KSE 파일에 같은 상품 여러 행 들어와도 한 번만 노출)
    pending = {}
    for r in unknown_rows:
        key = (r['상품명'], r['옵션명'])
        pending.setdefault(key, r)

    if not pending:
        return

    st.error(
        f"🆕 **신규 매핑 등록 필요 {len(pending)}건** — "
        "KSE 파일에 등장한 (상품명, 옵션) 중 매핑이 없는 항목. "
        "각각 다원 SKU 구성을 입력해 등록하세요. 모두 해결되어야 다원 발주서 다운로드 가능."
    )

    # 신규 등록 대상 요약
    summary = pd.DataFrame([
        {
            'Qoo10 상품명': k[0],
            'Qoo10 옵션': k[1] or '(없음)',
            '대표 주문번호': sample.get('주문번호', ''),
            '대표 접수번호': sample.get('접수번호', ''),
        }
        for k, sample in pending.items()
    ])
    st.markdown("**신규 등록 대상**")
    st.dataframe(
        summary, hide_index=True, width="stretch",
        column_config={
            'Qoo10 상품명': st.column_config.TextColumn(width="large"),
            'Qoo10 옵션': st.column_config.TextColumn(width="medium"),
            '대표 주문번호': st.column_config.TextColumn(width="small"),
            '대표 접수번호': st.column_config.TextColumn(width="small"),
        },
    )

    # 기존 매핑의 SKU 참고 (자동완성 X — 사용자가 코드 직접 입력)
    if known_skus:
        with st.expander(f"📋 기존 등록 SKU 참고 ({len(known_skus)}개) — 코드 복사용", expanded=False):
            st.dataframe(
                pd.DataFrame(known_skus), hide_index=True, width="stretch",
                column_config={
                    'sku_code': st.column_config.TextColumn('SKU 코드', width="medium"),
                    'sku_name': st.column_config.TextColumn('상품명', width="large"),
                },
            )

    items = list(pending.items())
    st.markdown("---")
    st.markdown(f"**📝 매핑 입력** — 총 {len(items)}건 (각 항목 다원 SKU 구성 입력 후 등록)")

    for idx, ((qname, qoption), sample) in enumerate(items):
        with st.container(border=True):
            st.markdown(f"**🆕 [{idx+1}/{len(items)}] {qname}**")
            st.caption(f"옵션: `{qoption or '(없음)'}`")

            ed_key = f"qkr_mapeditor_{idx}_{hash((qname, qoption))}"
            st.markdown("**다원 SKU 구성** (세트면 행 추가)")
            base = pd.DataFrame({
                'SKU 코드': [''],
                '상품명': [''],
                '수량': [1],
            })
            edited = st.data_editor(
                base,
                column_config={
                    'SKU 코드': st.column_config.TextColumn(
                        required=True, width="medium",
                        help="예) KC_8809885876166"),
                    '상품명': st.column_config.TextColumn(
                        required=False, width="large",
                        help="비고용 — 빈값이면 SKU 코드로 채워짐"),
                    '수량': st.column_config.NumberColumn(
                        min_value=1, step=1, default=1, required=True, width="small"),
                },
                num_rows="dynamic",
                key=ed_key,
                hide_index=True,
            )

            if st.button(
                "💾 매핑 등록",
                key=f"qkr_save_{ed_key}", type="primary",
            ):
                valid = edited[edited['SKU 코드'].astype(str).str.strip() != '']
                if valid.empty:
                    st.error("최소 1개 SKU 코드 필요.")
                else:
                    payload = []
                    for _, row in valid.iterrows():
                        code = str(row['SKU 코드']).strip()
                        name = str(row['상품명'] or '').strip() or code
                        qty = int(row['수량'] or 1)
                        payload.append((code, name, qty))
                    if _map.upsert(CHANNEL_KEY, qname, qoption, payload):
                        st.success(
                            "등록 완료: "
                            + " + ".join(f"{n}×{q}" for _, n, q in payload)
                        )
                        st.rerun()
                    else:
                        st.error("매핑 등록 실패 (DB 연결 확인)")


def render_page():
    _map.ensure_schema()
    st.markdown(
        "Qoo10 일본 주문 중 **한국 다원 → KSE 한국 → 일본** 출고 분량. "
        "KSE OMS 주문내역.xlsx + (선택) 라벨.pdf 한 번에 업로드."
    )

    uploaded_files = st.file_uploader(
        "KSE 어드민 파일 — 주문내역(.xlsx) + 쉽먼트 라벨(.pdf, 선택). 여러 개 가능",
        type=['xlsx', 'pdf'],
        accept_multiple_files=True,
        key="kse_oms_files",
        help="여러 xlsx/pdf 한꺼번에 끌어다 놓으세요. 확장자로 자동 분류됨.",
    )

    uploaded_xlsxs = [f for f in (uploaded_files or []) if f.name.lower().endswith('.xlsx')]
    uploaded_pdfs = [f for f in (uploaded_files or []) if f.name.lower().endswith('.pdf')]
    uploaded_pdf = uploaded_pdfs[0] if uploaded_pdfs else None  # passthrough 첫 번째 (사양 후속)

    chk_xlsx = f'✅ ({len(uploaded_xlsxs)})' if uploaded_xlsxs else ''
    chk_pdf = f'✅ ({len(uploaded_pdfs)})' if uploaded_pdfs else ''
    st.markdown(
        "<div style='font-size:0.8em'>\n\n"
        "| 파일 | 용도 | 업로드 |\n"
        "|------|------|:----:|\n"
        f"| `*.xlsx` | KSE OMS 주문내역 → 다원 발주서 변환 (여러 파일 합산) | {chk_xlsx} |\n"
        f"| `*.pdf` | 인박스 부착 라벨 → 파일명만 변경 | {chk_pdf} |\n\n"
        "</div>",
        unsafe_allow_html=True,
    )

    if not uploaded_xlsxs:
        with st.expander("📋 KSE OMS → 다원 19컬럼 매핑 (참고)", expanded=False):
            st.dataframe(_kse_mapping_table(), hide_index=True, width="stretch")
        return

    try:
        mappings = _map.load_for_channel(CHANNEL_KEY)
    except Exception as ex:
        st.error(f"channel_product_mapping 로드 실패: {ex}")
        return

    kse_rows = []
    parse_errors = []
    for f in uploaded_xlsxs:
        try:
            rows = parse_kse_oms_xlsx(f.getvalue())
            kse_rows.extend(rows)
        except Exception as ex:
            parse_errors.append(f"{f.name}: {ex}")
    if parse_errors:
        st.error("일부 파일 파싱 실패:\n" + "\n".join(parse_errors))
    if len(uploaded_xlsxs) > 1:
        st.caption(f"📂 {len(uploaded_xlsxs)}개 xlsx 합산 — 총 {len(kse_rows)} 행")

    if not kse_rows:
        st.warning("📭 KSE OMS 파일에 주문 데이터가 없습니다.")
        return

    result = kse_oms_to_daone_with_mapping(kse_rows, mappings)
    daone_rows = result['daone_rows']
    unknown = result['unknown_rows']
    incomplete = result['incomplete_rows']

    session_info = render_work_session_selector(CHANNEL_KEY, key_prefix='kse_kr')
    work_date = session_info['work_date']
    sequence = session_info['sequence']
    source_filename = ', '.join(f.name for f in uploaded_xlsxs)

    # 분기별 메트릭
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("KSE 행수", len(kse_rows))
    c2.metric("✅ 다원 행수 (펼침 후)", len(daone_rows))
    c3.metric("🆕 미매핑", len(unknown))
    c4.metric("⚠️ 미완전",
              len(incomplete),
              help="미완전: 매핑은 있으나 sku_codes='-' (다원 SKU 미입력)")

    if incomplete:
        with st.expander(
            f"⚠️ incomplete 매핑 {len(incomplete)}건 (item_codes는 있으나 sku_codes='-')",
            expanded=False,
        ):
            st.dataframe(pd.DataFrame(incomplete), hide_index=True, width="stretch")
            st.caption(
                "channel_product_mapping 의 sku_codes 가 '-' 인 매핑. "
                "사이드바 → 🗂 SKU 카탈로그 또는 Qoo10 일본 → 🔧 상품 매핑 에서 직접 갱신 필요."
            )

    # 신규 매핑 등록 (KSE 파일에 처음 등장한 상품/옵션)
    _render_pending_mappings(unknown, mappings)

    # 미매핑 있으면 다운로드 차단
    if unknown:
        return

    if not daone_rows:
        st.info("📭 다원 출고 대상 행이 없습니다.")
        return

    # 미리보기
    st.markdown("---")
    st.markdown("**미리보기**")
    df = pd.DataFrame(daone_rows)
    preview_cols = ['출하의뢰번호', '출하의뢰항번', '주문번호', '상품명', '제품코드',
                    '주문수량', '수취인명', '수취인우편번호', '수취인주소1', '송장번호', '택배사명']
    available = [c for c in preview_cols if c in df.columns]
    st.dataframe(df[available].head(50), width="stretch", hide_index=True)
    if len(df) > 50:
        st.caption(f"… 50/{len(df)} 행 표시")

    # 다운로드
    try:
        xlsx_bytes = build_daone_xlsx(daone_rows, add_packing_columns=True)
    except Exception as ex:
        st.error(f"다원 xlsx 생성 실패: {ex}")
        return

    # 부착문서 PDF — 아웃박스별 라벨
    try:
        packed_rows = compute_packing(list(daone_rows))
        attached_pdf_bytes = build_kse_attached_pdf(packed_rows, work_date)
    except Exception as ex:
        st.error(f"부착문서 PDF 생성 실패: {ex}")
        attached_pdf_bytes = None

    unique_orders = len({r.get('주문번호', '') for r in daone_rows if r.get('주문번호')})
    total_qty = sum(int(r.get('주문수량', 0) or 0) for r in daone_rows)

    yymmdd = work_date.strftime('%y%m%d')
    out_name = f"{yymmdd}_{int(sequence)}차발주서_큐텐국내(주문건수 {unique_orders}, 주문량수 {total_qty}).xlsx"
    c_dl, c_save = st.columns([2, 1])
    with c_dl:
        st.download_button(
            f"📥 {out_name}",
            data=xlsx_bytes,
            file_name=out_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary", width="stretch",
            key="kse_daone_download",
        )
    with c_save:
        render_save_button(CHANNEL_KEY, session_info, daone_rows,
                           source_filename, key_prefix='kse_kr')

    if attached_pdf_bytes:
        attached_name = f"{yymmdd}_{int(sequence)}차_KSE_부착문서.pdf"
        st.download_button(
            f"📥 {attached_name}",
            data=attached_pdf_bytes,
            file_name=attached_name,
            mime="application/pdf",
            type="primary", width="stretch",
            key="kse_attached_pdf_download",
        )

    if uploaded_pdf is not None:
        pdf_out_name = f"{yymmdd}_{int(sequence)}차_KSE쉽먼트라벨.pdf"
        st.download_button(
            f"📥 {pdf_out_name}",
            data=uploaded_pdf.getvalue(),
            file_name=pdf_out_name,
            mime="application/pdf",
            width="stretch",
            key="kse_pdf_download",
        )
        st.caption("📌 PDF 파일명 형식 사양 확정 후 업데이트 예정.")
