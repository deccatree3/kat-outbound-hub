"""
캐처스 메이커스 (카카오메이커스) 출고 페이지.

입력:
  - 메이커스 주문내역.xlsx (시트명 '주문내역', 22컬럼)

매핑:
  - channel_product_mapping (channel='cachers_makers')
  - 미매핑 (상품, 옵션) 발견 시 페이지 안에서 즉석 등록 (KSE 국내와 동일 패턴)
  - 잘못 등록된 매핑 수정/삭제는 어드민 → 🔧 상품 매핑

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
from outputs.eza.builder import build_makers_eza_xls
from outputs.makers.waybill import fill_makers_waybills
from channels._session_selector import (
    render_work_session_selector, render_save_button,
)


CHANNEL_KEY = 'cachers_makers'

OUTPUT_DAONE = '다원 발주서 (직접)'
OUTPUT_EZA = '이지어드민 발주서 (통합)'


def _mapping_table():
    return pd.DataFrame([
        {'메이커스': '— 고정 —',          '다원 19컬럼': '몰명(또는 몰코드) = "000000000001"'},
        {'메이커스': '— 고정 —',          '다원 19컬럼': '출하의뢰번호 = "[캐처스] 카카오메이커스"'},
        {'메이커스': '배송번호',          '다원 19컬럼': '출하의뢰항번'},
        {'메이커스': '주문번호',          '다원 19컬럼': '주문번호'},
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


def _render_pending_mappings(unknown_rows):
    """미매핑 (상품, 옵션) 즉석 등록 모달.
    각 항목별로 SKU 코드 + 상품명 + 수량 직접 입력. 등록 후 rerun.
    """
    known_skus = _map.list_known_skus()

    pending = {}
    for r in unknown_rows:
        key = (r['상품'], r['옵션'])
        pending.setdefault(key, r)

    if not pending:
        return

    st.error(
        f"🆕 **신규 매핑 등록 필요 {len(pending)}건** — "
        "각 항목별 SKU 구성 입력 후 등록하세요. 모두 해결되어야 다원 발주서 다운로드 가능."
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
    st.markdown("**신규 등록 대상**")
    st.dataframe(
        summary, hide_index=True, width="stretch",
        column_config={
            '상품': st.column_config.TextColumn(width="large"),
            '옵션': st.column_config.TextColumn(width="medium"),
            '대표 주문번호': st.column_config.TextColumn(width="small"),
            '대표 배송번호': st.column_config.TextColumn(width="small"),
        },
    )

    # 기존 등록된 SKU 참고
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
    st.markdown(f"**📝 매핑 입력** — 총 {len(items)}건")

    for idx, ((pname, poption), sample) in enumerate(items):
        with st.container(border=True):
            st.markdown(f"**🆕 [{idx+1}/{len(items)}] {pname}**")
            st.caption(f"옵션: `{poption or '(없음)'}`")

            ed_key = f"makers_mapeditor_{idx}_{hash((pname, poption))}"
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
                        help="예) NKVOLP250"),
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
                key=f"makers_save_{ed_key}", type="primary",
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
                    if _map.upsert(CHANNEL_KEY, pname, poption, payload):
                        st.success(
                            "등록 완료: "
                            + " + ".join(f"{n}×{q}" for _, n, q in payload)
                        )
                        st.rerun()
                    else:
                        st.error("매핑 등록 실패 (DB 연결 확인)")


def _render_daone_output(makers_rows, work_date, sequence, source_filename, session_info):
    """옵션 1 — 다원 발주서 직접 생성. SKU 매핑 필요."""
    try:
        mappings = _map.load_for_channel(CHANNEL_KEY)
    except Exception as ex:
        st.error(f"channel_product_mapping 로드 실패: {ex}")
        return

    result = makers_to_daone_with_mapping(makers_rows, mappings)
    daone_rows = result['daone_rows']
    unknown = result['unknown_rows']
    incomplete = result['incomplete_rows']

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

    _render_pending_mappings(unknown)

    if unknown:
        return

    if not daone_rows:
        st.info("📭 다원 출고 대상 행이 없습니다.")
        return

    st.markdown("---")
    st.markdown("**미리보기**")
    df = pd.DataFrame(daone_rows)
    preview_cols = ['출하의뢰번호', '출하의뢰항번', '주문번호', '상품명', '제품코드',
                    '주문수량', '수취인명', '수취인우편번호', '수취인주소1', '송장번호', '택배사명']
    available = [c for c in preview_cols if c in df.columns]
    st.dataframe(df[available].head(50), width="stretch", hide_index=True)
    if len(df) > 50:
        st.caption(f"… 50/{len(df)} 행 표시")

    try:
        xlsx_bytes = build_daone_xlsx(daone_rows)
    except Exception as ex:
        st.error(f"다원 xlsx 생성 실패: {ex}")
        return

    unique_orders = len({r.get('주문번호', '') for r in daone_rows if r.get('주문번호')})
    total_qty = sum(int(r.get('주문수량', 0) or 0) for r in daone_rows)

    yymmdd = work_date.strftime('%y%m%d')
    out_name = f"{yymmdd}_{int(sequence)}차발주서_메이커스(주문건수 {unique_orders}, 주문량수 {total_qty}).xlsx"
    c_dl, c_save = st.columns([2, 1])
    with c_dl:
        st.download_button(
            f"📥 {out_name}",
            data=xlsx_bytes,
            file_name=out_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary", width="stretch",
            key="makers_daone_download",
        )
    with c_save:
        render_save_button(CHANNEL_KEY, session_info, daone_rows,
                           source_filename, key_prefix='makers')
    st.caption("📤 다원에 단독 전달 또는 통합 발주서에 저장.")


def _render_eza_output(makers_rows, work_date, sequence):
    """옵션 2 — 이지어드민 발주서 (8컬럼). 매핑 미사용 — EZA가 자체 매핑 보유."""
    c1, c2 = st.columns(2)
    c1.metric("메이커스 행수", len(makers_rows))
    total_qty = sum(int(r.get('수량', 0) or 0) for r in makers_rows)
    c2.metric("총 수량", total_qty)

    # 미리보기
    st.markdown("---")
    st.markdown("**미리보기 (이지어드민 8컬럼)**")
    preview = pd.DataFrame([{
        '주문번호': r.get('주문번호', ''),
        '상품명': f"{r.get('상품', '')}_{r.get('옵션', '')}" if r.get('옵션') else r.get('상품', ''),
        '수량': r.get('수량', ''),
        '주문일': str(r.get('주문일시', ''))[:10],
        '수령인': r.get('수령인명', ''),
        '수령자연락처': r.get('수령인 연락처1', ''),
        '주소': r.get('배송주소', ''),
        '배송메모': r.get('배송메시지', ''),
    } for r in makers_rows[:50]])
    st.dataframe(preview, width="stretch", hide_index=True)
    if len(makers_rows) > 50:
        st.caption(f"… 50/{len(makers_rows)} 행 표시")

    try:
        xls_bytes = build_makers_eza_xls(makers_rows)
    except Exception as ex:
        st.error(f"이지어드민 xls 생성 실패: {ex}")
        return

    yymmdd = work_date.strftime('%y%m%d')
    out_name = f"카카오메이커스 발주서 업로드_{yymmdd}.xls"
    st.download_button(
        f"📥 {out_name}",
        data=xls_bytes,
        file_name=out_name,
        mime="application/vnd.ms-excel",
        type="primary", width="stretch",
        key="makers_eza_download",
    )
    st.caption(
        "📤 이지어드민에 업로드 → 다른 캐처스 채널과 통합되어 통합 다원 발주서로 출력. "
        "(다원에 별도 전달 불필요)"
    )


def _tab_create_order():
    st.markdown(
        "카카오메이커스 주문내역.xlsx 업로드 후 출력 형식 선택. "
        "다원 발주서 (직접) — SKU 매핑 필요 / 이지어드민 발주서 (통합) — 매핑 불필요."
    )

    uploaded_files = st.file_uploader(
        "메이커스 주문내역.xlsx (여러 개 가능)",
        type=['xlsx'],
        accept_multiple_files=True,
        key="makers_xlsx",
        help="이지오토 N — 메이커스에서 직접 다운로드한 주문내역 파일. 여러 개 한꺼번에 끌어다 놓을 수 있음.",
    )

    if not uploaded_files:
        with st.expander("📋 메이커스 → 다원 19컬럼 매핑 (참고)", expanded=False):
            st.dataframe(_mapping_table(), hide_index=True, width="stretch")
        return

    makers_rows = []
    parse_errors = []
    for f in uploaded_files:
        try:
            rows = parse_makers_xlsx(f.getvalue())
            makers_rows.extend(rows)
        except Exception as ex:
            parse_errors.append(f"{f.name}: {ex}")
    if parse_errors:
        st.error("일부 파일 파싱 실패:\n" + "\n".join(parse_errors))
    if len(uploaded_files) > 1:
        st.caption(f"📂 {len(uploaded_files)}개 파일 합산 — 총 {len(makers_rows)} 행")

    if not makers_rows:
        st.warning("📭 메이커스 파일에 주문 데이터가 없습니다.")
        return

    session_info = render_work_session_selector(CHANNEL_KEY, key_prefix='makers')
    work_date = session_info['work_date']
    sequence = session_info['sequence']
    source_filename = ', '.join(f.name for f in uploaded_files)

    output_kind = st.radio(
        "출력 형식",
        options=[OUTPUT_DAONE, OUTPUT_EZA],
        horizontal=True,
        key="makers_output_kind",
        help=(
            f"**{OUTPUT_DAONE}**: 메이커스 → 다원 19컬럼 발주서. SKU 매핑 필요. "
            "다원에 직접 전달 또는 통합 발주서에 저장.\n\n"
            f"**{OUTPUT_EZA}**: 메이커스 → 이지어드민 8컬럼 발주서. 매핑 불필요. "
            "이지어드민 업로드 후 다른 캐처스 채널과 통합되어 다원으로."
        ),
    )

    st.markdown("---")
    if output_kind == OUTPUT_DAONE:
        _render_daone_output(makers_rows, work_date, int(sequence),
                             source_filename, session_info)
    else:
        _render_eza_output(makers_rows, work_date, int(sequence))


def _tab_fill_waybill():
    st.markdown(
        "다원 채번.xls + 메이커스 원본 주문서.xlsx 업로드 → "
        "송장번호 채워진 메이커스 주문서.xlsx 다운로드 → 메이커스 어드민에 업로드."
    )
    st.caption(
        "매칭 키: (수령인명, 수령인 연락처1) — 같은 사람이 같은 날 여러 주문이면 "
        "다원 채번 순서대로 1:1 매칭. 매칭 실패는 미매칭 목록에 표시됩니다."
    )

    uploaded = st.file_uploader(
        "메이커스 원본.xlsx + 다원 채번.xls 한 번에 업로드",
        type=['xlsx', 'xls'],
        accept_multiple_files=True,
        key="makers_waybill_files",
        help="확장자로 자동 분류 (.xlsx → 메이커스 원본, .xls → 다원 채번)",
    )

    makers_xlsx = next((f for f in (uploaded or []) if f.name.lower().endswith('.xlsx')), None)
    daone_xls = next((f for f in (uploaded or []) if f.name.lower().endswith('.xls')), None)

    chk_x = '✅' if makers_xlsx else ''
    chk_d = '✅' if daone_xls else ''
    st.markdown(
        "<div style='font-size:0.8em'>\n\n"
        "| 파일 | 용도 | 업로드 |\n"
        "|------|------|:----:|\n"
        f"| `*.xlsx` | 메이커스 원본 주문서 (송장 채워질 양식) | {chk_x} |\n"
        f"| `*.xls` | 다원 채번 (운송장번호 source) | {chk_d} |\n\n"
        "</div>",
        unsafe_allow_html=True,
    )

    if not (makers_xlsx and daone_xls):
        return

    try:
        result_bytes, info = fill_makers_waybills(
            makers_xlsx.getvalue(), daone_xls.getvalue()
        )
    except Exception as ex:
        st.error(f"송장 기입 실패: {ex}")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("✅ 송장 기입", info['filled'])
    c2.metric("🆕 미매칭 (메이커스)", len(info['unmatched']))
    c3.metric("⚠️ 동일 키 중복", len(info['duplicates']))
    c4.metric("📦 잔여 채번", len(info['leftover_waybills']),
              help="다원 채번에 있으나 메이커스에 매칭 안 된 행 — 보통 0")

    if info['unmatched']:
        with st.expander(
            f"🆕 미매칭 메이커스 행 {len(info['unmatched'])}건 — 다원 채번에 없음",
            expanded=True,
        ):
            st.dataframe(pd.DataFrame(info['unmatched']),
                         hide_index=True, width="stretch")

    if info['duplicates']:
        with st.expander(
            f"⚠️ 동일 (수령인, 전화) 키 중복 매칭 {len(info['duplicates'])}건",
            expanded=False,
        ):
            st.dataframe(pd.DataFrame(info['duplicates']),
                         hide_index=True, width="stretch")
            st.caption("같은 사람이 같은 날 여러 주문 → 다원 채번 순서대로 1:1 매칭. 검수 권장.")

    if info['leftover_waybills']:
        with st.expander(
            f"📦 잔여 채번 {len(info['leftover_waybills'])}건 — 메이커스에 매칭 안 됨",
            expanded=False,
        ):
            st.dataframe(pd.DataFrame(info['leftover_waybills']),
                         hide_index=True, width="stretch")
            st.caption("두 파일의 사람/전화 표기 차이 또는 데이터 누락 가능. 검수 필요.")

    out_name = makers_xlsx.name.replace('.xlsx', '_송장기입.xlsx')
    st.download_button(
        f"📥 {out_name}",
        data=result_bytes,
        file_name=out_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", width="stretch",
        key="makers_waybill_download",
    )
    st.caption("📤 메이커스 어드민에 업로드.")


def render_page():
    _map.ensure_schema()
    tab_order, tab_waybill = st.tabs([
        "📤 발주서 생성", "📥 송장 기입"
    ])
    with tab_order:
        _tab_create_order()
    with tab_waybill:
        _tab_fill_waybill()
