"""
국내몰 Streamlit 페이지.

EZA 확장주문검색.xls 한 번 업로드 → 두 출력 동시 제공:
  - 다원 발주서.xlsx — 판매처그룹='캐처스' 행만 변환 (다원 수기 등록용)
  - 일반주문 번들작업건.xlsx — 판매처그룹≠'캐처스'(네뉴 등) 세트 주문만 양식 채움

EZA ↔ 다원 자동 연동은 네뉴만 활성. 캐처스는 다원 수기 업로드.
"""
import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import select

from outputs.daone.builder import (
    parse_eza_xls,
    transform_to_daone,
    build_daone_xlsx,
)
from outputs.nenu_bundle.builder import build_bundle_xlsx
from outputs.eza.builder import (
    build_eza_waybill_xlsx, EZA_WAYBILL_DEFAULT_CARRIER,
    parse_daone_invoice_xls, parse_3pl_invoice_xlsx, build_eza_waybill_from_triples,
    build_nenu_to_cachers_eza_xls,
)
from outputs.eza.cachers_nenu import (
    load_purchase_list, compute_affected_products, split_held_orders,
    STATUS_MOVE, STATUS_WATCH,
)
from rocketgrowth.ingestion.wms_file import (
    parse_wms_inventory_file, aggregate_wms_by_barcode,
)
from rocketgrowth.db import get_session
from rocketgrowth.models import WmsProduct
from outputs.cachers_3pl.builder import (
    build_cachers_3pl_xlsx, filter_target_rows as _3pl_filter, TARGET_SUPPLIER as _3PL_SUPPLIER,
)
from channels._session_selector import (
    render_work_session_selector, render_save_button,
)
from utils.timezone import kst_today


CHANNEL_KEY = 'domestic'


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


def _load_box_qty_by_code(purchase_list) -> dict:
    """매입리스트 바코드 → WmsProduct.box_qty → {캐처스품목코드: box입수|None}."""
    barcodes = sorted({p.barcode for p in purchase_list if p.barcode})
    box_by_bc: dict[str, int | None] = {}
    if barcodes:
        with get_session() as s:
            for bc, bq in s.execute(
                select(WmsProduct.wms_barcode, WmsProduct.box_qty)
                .where(WmsProduct.wms_barcode.in_(barcodes))
            ).all():
                box_by_bc[bc] = bq
    return {p.code: box_by_bc.get(p.barcode) for p in purchase_list}


def _parse_cachers_stock(data: bytes, name: str) -> dict:
    """캐처스 재고 Document_*.xls bytes → 품목코드별 집계 (RELEASEAREA 제외 포함)."""
    tmp = Path(f"./_tmp_cachers_stock_{name}")
    tmp.write_bytes(data)
    try:
        snap = parse_wms_inventory_file(tmp)
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
    return aggregate_wms_by_barcode(snap)


# 헤더 컬럼 시그니처로 .xls 종류 판별 (둘 다 .xls 라 확장자로 구분 불가)
_STOCK_HEADER_HINTS = {'품목코드', '가능수량', '재고수량', 'LOC그룹', '품목손상플래그'}
_EZA_HEADER_HINTS = {'판매처그룹', '상품메모', '출하의뢰번호', '수취인명', '상품수량'}


def _classify_domestic_xls(data: bytes, name: str) -> str:
    """업로드 .xls 를 'stock'(캐처스 WMS 재고현황) / 'eza'(확장주문검색) / 'unknown' 분류.

    1순위: 헤더 행 컬럼명 시그니처. 2순위(헤더 모호 시): 파일명 'Document_' 접두.
    """
    import xlrd  # noqa: WPS433
    headers: set[str] = set()
    try:
        wb = xlrd.open_workbook(file_contents=data)
        ws = wb.sheet_by_index(0)
        if ws.nrows > 0:
            headers = {str(ws.cell_value(0, c)).strip() for c in range(ws.ncols)}
    except Exception:
        headers = set()

    stock_hit = len(headers & _STOCK_HEADER_HINTS)
    eza_hit = len(headers & _EZA_HEADER_HINTS)
    if stock_hit and stock_hit >= eza_hit:
        return 'stock'
    if eza_hit:
        return 'eza'
    if name.startswith('Document_'):
        return 'stock'
    return 'unknown'


def _render_daone_download(daone_rows, work_date, sequence, source_filename, session_info):
    """다원 발주서 미리보기 + 다운로드 + 저장 (홀딩 제외 후 행 기준)."""
    if not daone_rows:
        st.info("📭 전 주문이 홀딩되어 이번 차수 다원 발주서가 비었습니다.")
        return
    unique_orders, total_qty = _render_metrics_and_preview(daone_rows)
    try:
        xlsx_bytes = build_daone_xlsx(daone_rows)
    except Exception as ex:
        st.error(f"다원 xlsx 생성 실패: {ex}")
        return
    yymmdd = work_date.strftime('%y%m%d')
    out_name = f"{yymmdd}_{int(sequence)}차발주서(주문건수 {unique_orders}, 주문수량 {total_qty}).xlsx"
    c_dl, c_save = st.columns([2, 1])
    with c_dl:
        st.download_button(
            f"📥 {out_name}",
            data=xlsx_bytes,
            file_name=out_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary", width="stretch",
            key="daone_download",
        )
    with c_save:
        render_save_button(CHANNEL_KEY, session_info, daone_rows,
                           source_filename, key_prefix='domestic')
    st.caption("📤 다원 WMS에 수기 업로드 (단독) 또는 통합 발주서에 저장.")


def _section_daone(eza_rows, work_date, sequence, source_filename, session_info,
                   stock_file=None):
    st.markdown("### 📋 [캐처스]다원 출고요청")
    st.caption(
        f"판매처그룹='캐처스' 행만 변환. 공급처='{_3PL_SUPPLIER}' 행은 추가 제외 "
        "(별도 [캐처스]3PL-자연미앤 섹션에서 처리). "
        "캐처스 WMS 재고현황 동시 업로드 시 네뉴 매입리스트 품절분 합포장 홀딩."
    )

    cachers_rows = [
        r for r in eza_rows
        if str(r.get('판매처그룹', '')).strip() == '캐처스'
        and str(r.get('공급처', '')).strip() != _3PL_SUPPLIER
    ]
    if not cachers_rows:
        st.info("📭 캐처스 행이 없어 다원 발주서를 생성하지 않습니다.")
        return

    daone_rows_all = transform_to_daone(cachers_rows)

    # 재고파일 없으면: 기존대로 전체 발주서 (홀딩 분석 안 함)
    if stock_file is None:
        st.markdown("---")
        _render_daone_download(daone_rows_all, work_date, sequence,
                               source_filename, session_info)
        return

    try:
        stock_agg = _parse_cachers_stock(stock_file.getvalue(), stock_file.name)
        purchase_list = load_purchase_list()
        box_by_code = _load_box_qty_by_code(purchase_list)
        affected = compute_affected_products(
            daone_rows_all, stock_agg, purchase_list, box_by_code,
        )
    except Exception as ex:
        st.error(f"재고 홀딩 분석 실패: {ex} — 홀딩 없이 전체 발주서로 진행.")
        affected = []

    if not affected:
        st.success("✅ 매입리스트 품절/부족/관찰 대상 없음 — 전체 출고.")
        st.markdown("---")
        _render_daone_download(daone_rows_all, work_date, sequence,
                               source_filename, session_info)
        return

    move_codes = {a.code for a in affected if a.status == STATUS_MOVE}
    n_move = len(move_codes)
    n_watch = len(affected) - n_move
    st.warning(
        f"⚠️ 매입리스트 검토. 이동필요 {n_move}건, "
        f"관찰(가용재고가 box인입수 대비 50%이하) {n_watch}건"
    )
    df = pd.DataFrame([{
        "재고이동": a.status == STATUS_MOVE,
        "상태": a.status,
        "상품명": a.name,
        "품목코드": a.code,
        "주문수량": a.ordered,
        "가용재고": a.available,
        "box입수": a.box_qty if a.box_qty is not None else 0,
        "이동수량": int(a.box_qty) if a.box_qty else int(a.ordered),
    } for a in affected])
    edited = st.data_editor(
        df, hide_index=True, width="stretch",
        disabled=["상태", "상품명", "품목코드", "주문수량", "가용재고", "box입수"],
        column_config={
            "재고이동": st.column_config.CheckboxColumn(
                "재고이동", help="이동필요는 체크와 무관하게 항상 제외. 관찰만 선택 반영."),
            "이동수량": st.column_config.NumberColumn(
                "이동수량", min_value=0, step=1,
                help="이지어드민 발주 수량 (기본=box입수, 수정 가능)"),
        },
        key="domestic_holding_editor",
    )
    st.caption(
        "ℹ️ 이동필요(품절·부족)는 체크 해제해도 **항상** 출고요청서에서 제외. "
        "관찰은 체크한 것만 제외/재고이동."
    )

    # 제외 대상 = 이동필요(무조건) + 관찰(체크). 이지어드민 발주 품목도 동일 집합.
    watch_checked = {
        str(r["품목코드"]) for _, r in edited.iterrows()
        if r["상태"] == STATUS_WATCH and bool(r["재고이동"])
    }
    held_codes = move_codes | watch_checked
    qty_by_code = {str(r["품목코드"]): int(r["이동수량"]) for _, r in edited.iterrows()}
    name_by_code = {str(r["품목코드"]): r["상품명"] for _, r in edited.iterrows()}

    if st.button(
        "✅ 재고이동 확정",
        type="primary", width="stretch", key="domestic_holding_confirm",
    ):
        st.session_state["domestic_holding_confirmed"] = {
            "codes": sorted(held_codes),
            "items": [
                {"name": name_by_code[c], "qty": qty_by_code.get(c, 0)}
                for c in sorted(held_codes)
            ],
        }

    conf = st.session_state.get("domestic_holding_confirmed")
    if not conf:
        st.info("👆 '재고이동 확정'을 누르면 이지어드민 발주서 → 출고요청서가 생성됩니다.")
        return

    held_set = set(conf["codes"])
    shipped, held, n_groups = split_held_orders(daone_rows_all, held_set)
    m1, m2, m3 = st.columns(3)
    m1.metric("홀딩 합포장 그룹", n_groups)
    m2.metric("출고요청서 제외 행수", len(held))
    m3.metric("이지어드민 발주 품목", len(conf["items"]))

    # ① 이지어드민 발주서 (먼저)
    st.markdown("---")
    try:
        eza_xls = build_nenu_to_cachers_eza_xls(conf["items"], work_date)
    except Exception as ex:
        eza_xls = None
        st.error(f"이지어드민 발주서 생성 실패: {ex}")
    eza_name = (
        f"{work_date.strftime('%y%m%d')}_{int(sequence)}차_"
        f"네뉴→캐처스_이지어드민발주서({len(conf['items'])}품목).xls"
    )
    st.download_button(
        f"📥 ① 이지어드민 발주서 (네뉴→캐처스 재고이동) — {len(conf['items'])}품목",
        data=eza_xls if eza_xls else b"",
        file_name=eza_name,
        mime="application/vnd.ms-excel",
        type="primary", width="stretch",
        disabled=eza_xls is None,
        key="domestic_nenu_cachers_eza_dl",
    )
    st.caption("📤 네뉴 이지어드민 업로드 → 재고차감 → 네뉴→캐처스 재고이동. 다음 차수에 홀딩분 출고.")

    # ② 출고요청서 (다원 발주서) — 그 아래
    st.markdown("---")
    _render_daone_download(shipped, work_date, sequence, source_filename, session_info)


def _section_bundle(eza_bytes_list, work_date, sequence):
    st.markdown("### 📦 [네뉴]번들작업요청")
    st.caption(
        "이지어드민에서 판매처그룹='캐처스' 행 + 상품명에 **'선물세트'** 미포함 행은 자동 제외. "
        "마스터 양식의 세트 행 D셀에 이지어드민 합계 정수 입력. 단품 출고수량(C)은 Excel SUMIFS로 자동 계산."
    )

    try:
        xlsx_bytes, info = build_bundle_xlsx(eza_bytes_list, work_date, int(sequence))
    except Exception as ex:
        st.error(f"번들작업파일 생성 실패: {ex}")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("세트 매칭/채움", f"{len(info['set_matched_barcodes'])} / {info['set_rows_filled']}")
    c2.metric("총 세트 입고수량", info['total_qty'])
    c3.metric("단품 직접 주문 (참고)", len(info['single_matched_barcodes']))
    c4.metric("이지어드민(네뉴) 종/수량", f"{info['eza_total_rows']} / {info['eza_total_qty']}")
    st.caption(f"마스터 = 단품 {info['master_single_count']}개 + 세트 {info['master_set_count']}개.")

    if info['unmatched_barcodes']:
        st.warning(
            f"⚠️ 이지어드민(네뉴)에 있으나 마스터에 없는 바코드 **{len(info['unmatched_barcodes'])}건** — "
            "신규 SKU 또는 마스터 누락 가능성. `outputs/nenu_bundle/template.xlsx` 검토 필요."
        )
        with st.expander("미매칭 바코드 목록", expanded=False):
            st.code('\n'.join(info['unmatched_barcodes']))

    if info['single_matched_barcodes']:
        with st.expander(
            f"📦 단품 직접 주문 {len(info['single_matched_barcodes'])}건 "
            "(양식에 자리 없음 — 이지어드민↔다원 자동 흐름이 처리)",
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


def _section_3pl(eza_rows, work_date, sequence):
    """캐처스 3PL-자연미앤 출고요청서 (공급처 필터)."""
    st.markdown("### 🥡 [캐처스]3PL-자연미앤")
    st.caption(
        f"이지어드민 의 공급처 = `{_3PL_SUPPLIER}` 행만 추출. 25컬럼 양식. "
        "몰명 컬럼은 빈값 (이지어드민 에 없음)."
    )

    target = _3pl_filter(eza_rows)
    n = len(target)
    unique_orders = len({
        str(r.get('고객주문번호') or r.get('주문번호') or '').strip()
        for r in target
        if (r.get('고객주문번호') or r.get('주문번호'))
    })
    total_qty = sum(int(r.get('주문수량', 0) or 0) for r in target)

    c1, c2, c3 = st.columns(3)
    c1.metric("주문 행수", n)
    c2.metric("주문번호 (고유)", unique_orders)
    c3.metric("주문수량 합계", total_qty)

    if n == 0:
        st.info(f"📭 공급처 = `{_3PL_SUPPLIER}` 인 행이 없어 출고요청서를 생성하지 않습니다.")
        return

    try:
        xlsx_bytes, _ = build_cachers_3pl_xlsx(eza_rows)
    except Exception as ex:
        st.error(f"3PL 출고요청서 생성 실패: {ex}")
        return

    yymmdd = work_date.strftime('%y%m%d')
    out_name = f"{yymmdd}_{int(sequence)}차_3PL자연미앤출고요청서(주문건수 {unique_orders}, 주문수량 {total_qty}).xls"
    st.download_button(
        f"📥 {out_name}",
        data=xlsx_bytes,
        file_name=out_name,
        mime="application/vnd.ms-excel",
        type="primary", width="stretch",
        key=f"3pl_download_{work_date}_{sequence}",
    )
    st.caption("📤 3PL 측에 수기 전달.")


def _tab_create_order():
    st.markdown(
        "이지어드민 **확장주문검색.xls** + 캐처스 **WMS 재고현황.xls** 업로드"
    )

    uploaded = st.file_uploader(
        "확장주문검색(.xls, 여러 개) + 캐처스 WMS 재고현황 Document_*.xls 한 번에",
        type=['xls'],
        accept_multiple_files=True,
        key="domestic_eza",
        help="이지어드민 확장주문검색 + (선택) EZA WMS 재고현황을 함께 끌어다 놓으면 "
             "헤더로 자동 구분. 재고현황: 품목코드=캐처스 품목코드, RELEASEAREA LOC "
             "제외 후 가능수량 합산.",
    )

    if not uploaded:
        return

    # 헤더 기반 자동 분류 — 확장주문검색 vs 캐처스 WMS 재고현황
    uploaded_files, stock_file, unknowns = [], None, []
    for f in uploaded:
        kind = _classify_domestic_xls(f.getvalue(), f.name)
        if kind == 'stock':
            if stock_file is None:
                stock_file = f
            else:
                st.warning(f"⚠️ 재고현황 파일이 2개 이상 — '{f.name}' 무시 (먼저 올린 것 사용).")
        elif kind == 'eza':
            uploaded_files.append(f)
        else:
            unknowns.append(f.name)
            uploaded_files.append(f)  # 미상은 확장주문검색으로 가정 (파싱 단계서 걸러짐)

    st.caption(
        f"🔎 자동 분류 — 확장주문검색 {len(uploaded_files)} / "
        f"재고현황 {'1 (' + stock_file.name + ')' if stock_file else '0 (홀딩 없음)'}"
        + (f" / ⚠️미상 {len(unknowns)}: {', '.join(unknowns)}" if unknowns else "")
    )

    if not uploaded_files:
        st.warning("📭 확장주문검색 파일이 없습니다 (재고현황만으로는 발주서 생성 불가).")
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
        st.warning("📭 이지어드민 파일에 주문 데이터가 없습니다.")
        return

    session_info = render_work_session_selector(CHANNEL_KEY, key_prefix='domestic')
    work_date = session_info['work_date']
    sequence = session_info['sequence']
    source_filename = ', '.join(f.name for f in uploaded_files)

    _section_daone(eza_rows, work_date, int(sequence), source_filename, session_info,
                   stock_file=stock_file)
    st.markdown("---")
    _section_bundle([f.getvalue() for f in uploaded_files], work_date, int(sequence))
    st.markdown("---")
    _section_3pl(eza_rows, work_date, int(sequence))


def _tab_eza_waybill():
    st.markdown(
        "다원 채번.xls + 3PL 출고요청서.xlsx (송장 채워진) → **이지어드민 송장 업로드 양식.xlsx**. "
        "두 source 합산해서 1개 파일로. 한 가지만 업로드해도 OK."
    )

    uploaded = st.file_uploader(
        "다원 채번 (.xls) + 3PL 송장 채운 출고요청서 (.xlsx) 한 번에 업로드 (여러 개 가능)",
        type=['xls', 'xlsx'],
        accept_multiple_files=True,
        key="domestic_waybill_files",
        help="확장자로 자동 분류 (.xls → 다원 채번, .xlsx → 3PL 출고요청서).",
    )
    if not uploaded:
        return

    carrier = EZA_WAYBILL_DEFAULT_CARRIER  # 고정: CJ대한통운
    st.caption(f"📦 택배사 = `{carrier}` 고정. 양식의 관리번호 = 주문번호(고객주문번호).")

    daone_files = [f for f in uploaded if f.name.lower().endswith('.xls')]
    threepl_files = [f for f in uploaded if f.name.lower().endswith('.xlsx')]

    chk_d = f'✅ ({len(daone_files)})' if daone_files else ''
    chk_3 = f'✅ ({len(threepl_files)})' if threepl_files else ''
    st.markdown(
        "<div style='font-size:0.8em'>\n\n"
        "| 파일 | 용도 | 업로드 |\n"
        "|------|------|:----:|\n"
        f"| `*.xls` | 다원 채번 (12컬럼) — 택배사 default 적용 | {chk_d} |\n"
        f"| `*.xlsx` | 3PL 출고요청서 (송장/택배사 채워짐) | {chk_3} |\n\n"
        "</div>",
        unsafe_allow_html=True,
    )

    all_triples = []
    all_skipped = []
    parse_errors = []

    for f in daone_files:
        try:
            triples, skipped = parse_daone_invoice_xls(f.getvalue(), default_carrier=carrier)
            all_triples.extend(triples)
            for s in skipped:
                s.setdefault('파일', f.name)
            all_skipped.extend(skipped)
        except Exception as ex:
            parse_errors.append(f"{f.name}: {ex}")

    for f in threepl_files:
        try:
            triples, skipped = parse_3pl_invoice_xlsx(f.getvalue(), default_carrier=carrier)
            all_triples.extend(triples)
            for s in skipped:
                s.setdefault('파일', f.name)
            all_skipped.extend(skipped)
        except Exception as ex:
            parse_errors.append(f"{f.name}: {ex}")

    if parse_errors:
        st.error("일부 파일 파싱 실패:\n" + "\n".join(parse_errors))

    c1, c2, c3 = st.columns(3)
    c1.metric("✅ 송장 기입", len(all_triples))
    c2.metric("⚠️ skip", len(all_skipped),
              help="주문번호/송장번호 빈 행 — 양식에서 제외")
    unique_carriers = sorted({t[0] for t in all_triples})
    c3.metric("📦 택배사 종류", len(unique_carriers),
              help='/'.join(unique_carriers) if unique_carriers else '')

    if all_skipped:
        with st.expander(f"⚠️ skip {len(all_skipped)}건", expanded=False):
            st.dataframe(pd.DataFrame(all_skipped), hide_index=True, width="stretch")

    if not all_triples:
        st.info("📭 송장 기입할 행이 없습니다.")
        return

    try:
        xlsx_bytes = build_eza_waybill_from_triples(all_triples)
    except Exception as ex:
        st.error(f"송장 양식 생성 실패: {ex}")
        return

    today_str = kst_today().strftime('%y%m%d')
    out_name = f"{today_str}_이지어드민_송장업로드양식({len(all_triples)}건).xlsx"
    st.download_button(
        f"📥 {out_name}",
        data=xlsx_bytes,
        file_name=out_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", width="stretch",
        key="domestic_waybill_download",
    )
    st.caption("📤 이지어드민 송장 일괄 등록 양식으로 업로드.")


def render_page():
    tab_order, tab_waybill = st.tabs([
        "📤 발주서 생성", "📥 송장 양식 생성"
    ])
    with tab_order:
        _tab_create_order()
    with tab_waybill:
        _tab_eza_waybill()
