"""
캐처스 큐텐 국내출고 Streamlit 페이지.

Qoo10 일본 주문 중 한국 다원 → KSE 한국 → 일본 흐름.

입력:
  - KSE OMS 주문내역.xlsx (필수)
  - KSE 쉽먼트 라벨.pdf (선택, 인박스 부착)

매핑 흐름 (qoo10_product_mapping 활용 — Qoo10 일본 빌더와 enabled 분기 반대):
  매핑 없음            → 신규 등록 모달 (sku_codes 필수, KR 카탈로그 드롭다운, enabled=False 디폴트)
  매핑 enabled=True   → 일본 KSE 출고 대상 (다원 제외) — 경고
  매핑 enabled=False  → 다원 출고 대상. sku_codes 펼쳐서 1→N 다원 행
  매핑 enabled=False, sku_codes='-' → 다원 SKU 미입력 — 매핑 갱신 필요

출력:
  - 다원 발주서.xlsx (다원 표준 19컬럼)
  - PDF 라벨 파일명 변경 후 다운로드 (사양 후속)
"""
import datetime

import pandas as pd
import streamlit as st

from db import sku_catalog as sc
from outputs.daone.builder import (
    parse_kse_oms_xlsx,
    kse_oms_to_daone_with_mapping,
    build_daone_xlsx,
)
from qoo10 import generator as qgen


def _kse_mapping_table():
    return pd.DataFrame([
        {'KSE OMS': '판매마켓',                    '다원 19컬럼': '출하의뢰번호'},
        {'KSE OMS': '접수번호',                     '다원 19컬럼': '출하의뢰항번'},
        {'KSE OMS': '주문번호',                     '다원 19컬럼': '고객주문번호'},
        {'KSE OMS': '상품명 + 옵션명',              '다원 19컬럼': '상품명'},
        {'KSE OMS': 'qoo10_product_mapping 조회 → SKU', '다원 19컬럼': '제품코드 (1→N 펼침)'},
        {'KSE OMS': '수량',                         '다원 19컬럼': '주문수량 = SKU단위수량 × KSE수량'},
        {'KSE OMS': '받는사람',                     '다원 19컬럼': '주문자명 / 수취인명'},
        {'KSE OMS': '받는사람전화',                  '다원 19컬럼': '주문자연락처1 / 수취인연락처1'},
        {'KSE OMS': '우편번호',                     '다원 19컬럼': '수취인우편번호'},
        {'KSE OMS': '주소',                         '다원 19컬럼': '수취인주소1 = 주소2'},
        {'KSE OMS': '도착지송장번호',                '다원 19컬럼': '송장번호'},
        {'KSE OMS': '배송타입 (KSE)',               '다원 19컬럼': '택배사명'},
        {'KSE OMS': '— 고정 —',                     '다원 19컬럼': '몰명(또는 몰코드) = "000000000001"'},
    ])


def _render_kr_sku_quick_add(expanded: bool = False):
    """KR 카탈로그에 즉석 SKU 추가 — 매핑 등록 흐름과 같이 사용."""
    with st.expander("➕ 새 KR SKU 즉석 등록 (등록 후 아래 매핑 드롭다운에 바로 표시)",
                     expanded=expanded):
        c1, c2, c3 = st.columns([2, 3, 1])
        with c1:
            new_code = st.text_input(
                "SKU 코드 *", key="kr_quick_code",
                placeholder="예) KC_8809885876166",
            )
        with c2:
            new_name = st.text_input(
                "상품명 (선택)", key="kr_quick_name",
                placeholder="예) NUKIT VOLCANO PEELING AMPOULE",
            )
        with c3:
            st.write(" ")  # spacer
            if st.button("➕ 추가", type="primary", width="stretch", key="kr_quick_add_btn"):
                code = (new_code or '').strip()
                if not code:
                    st.error("SKU 코드 필수")
                elif sc.upsert_sku(code, 'KR', new_name, True, ''):
                    st.success(f"카탈로그 등록: {code}")
                    st.rerun()
                else:
                    st.error("등록 실패 (DB 연결 확인)")


def _render_pending_mappings(unknown_rows, incomplete_rows, mappings):
    """미매핑(신규) + 미완전(sku_codes='-') 행 모두 페이지 내에서 등록/갱신.

    UI는 (qoo10_name, qoo10_option) 키 단위로 expander 1개씩.
    add_mapping 은 ON CONFLICT UPSERT 라 신규/갱신 동일 호출.
    """
    sku_catalog_kr = sc.list_skus(location='KR', enabled_only=True)

    # 키 단위로 합치기 — 상태 = 'new' | 'update'
    pending = {}  # (name, option) -> {'status': 'new'/'update', 'sample': row}
    for r in unknown_rows:
        key = (r['상품명'], r['옵션명'])
        pending.setdefault(key, {'status': 'new', 'sample': r})
    for r in incomplete_rows:
        key = (r['상품명'], r['옵션명'])
        # incomplete 는 update 우선
        pending[key] = {'status': 'update', 'sample': r}

    if not pending:
        return

    n_new = sum(1 for v in pending.values() if v['status'] == 'new')
    n_upd = sum(1 for v in pending.values() if v['status'] == 'update')
    msg = []
    if n_new:
        msg.append(f"🆕 신규 매핑 **{n_new}건**")
    if n_upd:
        msg.append(f"♻️ 매핑 갱신 (sku_codes 미입력) **{n_upd}건**")
    st.error(
        " · ".join(msg) + " — 각 항목에 KR(다원) SKU + 단위수량을 입력해 등록/갱신하세요. "
        "모두 해결되어야 다원 발주서를 다운로드할 수 있습니다."
    )

    # KR SKU 즉석 등록 폼 (카탈로그 비어있으면 펼친 상태)
    _render_kr_sku_quick_add(expanded=not sku_catalog_kr)

    if not sku_catalog_kr:
        st.info(
            "위 폼으로 KR SKU를 먼저 등록하면 매핑 모달의 드롭다운에 즉시 반영됩니다. "
            "(다수 SKU 일괄 등록은 사이드바 → 🗂 KSE SKU 마스터 → 🇰🇷 KR 탭)"
        )
        return

    sku_options = [f"{s['sku_name']} ({s['sku_code']})" if s['sku_name'] else s['sku_code']
                   for s in sku_catalog_kr]
    sku_by_label = {lbl: s for lbl, s in zip(sku_options, sku_catalog_kr)}
    sku_by_code = {s['sku_code']: s for s in sku_catalog_kr}

    items = list(pending.items())
    for idx, ((qname, qoption), meta) in enumerate(items):
        status = meta['status']
        e = meta['sample']
        existing = mappings.get((qname, qoption)) if status == 'update' else None

        icon = '🆕' if status == 'new' else '♻️'
        verb = '등록' if status == 'new' else '갱신'

        with st.expander(
            f"{icon} 매핑 {verb} [{idx+1}/{len(items)}] : {qname[:50]}..."
            + (f" / {qoption[:40]}" if qoption else ""),
            expanded=(idx == 0),
        ):
            st.caption(f"**Qoo10 상품명**: `{qname}`")
            st.caption(f"**Qoo10 옵션**: `{qoption or '(없음)'}`")

            if existing:
                st.caption(
                    f"기존 매핑: enabled=`{existing['enabled']}`, "
                    f"item_codes=`{','.join(existing.get('item_codes', []))}`, "
                    f"sku_codes=`{','.join(existing.get('sku_codes', []))}`, "
                    f"quantities=`{','.join(str(q) for q in existing.get('quantities', []))}` "
                    "→ KR SKU 로 sku_codes 갱신"
                )

            st.markdown("**KR(다원) SKU 구성** (세트면 행 추가)")

            # incomplete 면 기존 quantities 보존, sku만 빈/첫 옵션으로
            if status == 'update' and existing:
                qtys = existing.get('quantities') or [1]
                init_skus = [(sku_options[0], q) for q in qtys]
            else:
                init_skus = [(sku_options[0], 1)]

            default_df = pd.DataFrame({
                'KR SKU': [s for s, _ in init_skus],
                '수량': [q for _, q in init_skus],
            })
            ed_key = f"qkr_mapeditor_{idx}_{hash((qname, qoption))}"
            edited = st.data_editor(
                default_df,
                column_config={
                    'KR SKU': st.column_config.SelectboxColumn(
                        options=sku_options, required=True, width="large",
                        help="🗂 KSE SKU 마스터 KR 탭에서 등록한 SKU"),
                    '수량': st.column_config.NumberColumn(
                        min_value=1, step=1, default=1, required=True, width="small"),
                },
                num_rows="dynamic",
                key=ed_key,
                hide_index=True,
            )

            if st.button(
                f"💾 매핑 {verb} (enabled=False, 다원 출고)",
                key=f"qkr_save_{ed_key}", type="primary",
            ):
                valid = edited.dropna(subset=['KR SKU'])
                if valid.empty:
                    st.error("최소 1개 SKU 필요.")
                else:
                    payload = []
                    for _, row in valid.iterrows():
                        info = sku_by_label[row['KR SKU']]
                        qty = int(row['수량'] or 1)
                        payload.append((info['sku_code'], info['sku_name'] or info['sku_code'], qty))
                    try:
                        qgen.add_mapping(qname, qoption, payload, enabled=False)
                        st.success(
                            f"매핑 {verb} 완료 (KSE 국내 출고 대상): "
                            + " + ".join(f"{n}×{q}" for _, n, q in payload)
                        )
                        st.rerun()
                    except Exception as ex:
                        st.error(f"실패: {ex}")


def render_page():
    sc.ensure_schema()
    st.markdown(
        "Qoo10 일본 주문 중 **한국 다원 → KSE 한국 → 일본** 출고 분량. "
        "KSE OMS 주문내역.xlsx + (선택) 라벨.pdf 한 번에 업로드."
    )

    uploaded_files = st.file_uploader(
        "KSE 어드민 파일 — 주문내역(.xlsx) + 쉽먼트 라벨(.pdf, 선택)",
        type=['xlsx', 'pdf'],
        accept_multiple_files=True,
        key="kse_oms_files",
        help="두 파일을 같이 끌어다 놓으세요. 확장자로 자동 분류됨.",
    )

    uploaded_xlsx = next((f for f in (uploaded_files or []) if f.name.lower().endswith('.xlsx')), None)
    uploaded_pdf = next((f for f in (uploaded_files or []) if f.name.lower().endswith('.pdf')), None)

    chk_xlsx = '✅' if uploaded_xlsx else ''
    chk_pdf = '✅' if uploaded_pdf else ''
    st.markdown(
        "<div style='font-size:0.8em'>\n\n"
        "| 파일 | 용도 | 업로드 |\n"
        "|------|------|:----:|\n"
        f"| `*.xlsx` | KSE OMS 주문내역 → 다원 발주서 변환 | {chk_xlsx} |\n"
        f"| `*.pdf` | 인박스 부착 라벨 → 파일명만 변경 | {chk_pdf} |\n\n"
        "</div>",
        unsafe_allow_html=True,
    )

    if not uploaded_xlsx:
        with st.expander("📋 KSE OMS → 다원 19컬럼 매핑 (참고)", expanded=False):
            st.dataframe(_kse_mapping_table(), hide_index=True, width="stretch")
        return

    try:
        kse_rows = parse_kse_oms_xlsx(uploaded_xlsx.getvalue())
    except Exception as ex:
        st.error(f"KSE OMS 파일 파싱 실패: {ex}")
        return

    if not kse_rows:
        st.warning("📭 KSE OMS 파일에 주문 데이터가 없습니다.")
        return

    # 매핑 로드 + 분기
    try:
        mappings = qgen.load_mappings()
    except Exception as ex:
        st.error(f"qoo10_product_mapping 로드 실패: {ex}")
        return

    result = kse_oms_to_daone_with_mapping(kse_rows, mappings)
    daone_rows = result['daone_rows']
    unknown = result['unknown_rows']
    not_for_daone = result['not_for_daone_rows']
    incomplete = result['incomplete_rows']

    # 작업일/차수
    today = datetime.date.today()
    c_d, c_s = st.columns([1, 1])
    work_date = c_d.date_input("작업일", value=today, key="kse_work_date")
    sequence = c_s.number_input(
        "차수", min_value=1, value=1, step=1, key="kse_sequence",
        help="같은 날 재실행 시 직접 +1 변경.",
    )

    # 분기별 메트릭
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("KSE 행수", len(kse_rows))
    c2.metric("✅ 다원 행수 (펼침 후)", len(daone_rows))
    c3.metric("🆕 미매핑", len(unknown))
    c4.metric("⚠️ 미완전/혼선",
              f"{len(incomplete)} / {len(not_for_daone)}",
              help="미완전: 매핑 있으나 sku_codes='-' / 혼선: enabled=True (일본 KSE 대상)")

    if not_for_daone:
        with st.expander(
            f"⚠️ enabled=True 행 {len(not_for_daone)}건 (일본 KSE 출고 대상 — 다원으로 안 보냄)",
            expanded=False,
        ):
            st.dataframe(pd.DataFrame(not_for_daone), hide_index=True, width="stretch")
            st.caption("이 행들은 다원 발주서에서 자동 제외됩니다. KSE OMS 다운에 들어온 게 잘못된 흐름이면 데이터 확인 필요.")

    # 신규 + 갱신 매핑을 페이지 내에서 한 번에 처리
    _render_pending_mappings(unknown, incomplete, mappings)

    # 미매핑 / 미완전 있으면 다운로드 차단
    if unknown or incomplete:
        return

    if not daone_rows:
        st.info("📭 다원 출고 대상 행이 없습니다 (모든 매핑이 enabled=True 인 상태).")
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
    out_name = f"{yymmdd}_{int(sequence)}차발주서_큐텐국내(주문건수 {unique_orders}, 주문량수 {total_qty}).xlsx"
    st.download_button(
        f"📥 {out_name}",
        data=xlsx_bytes,
        file_name=out_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", width="stretch",
        key="kse_daone_download",
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
