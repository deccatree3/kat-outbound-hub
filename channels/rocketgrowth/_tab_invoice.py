"""탭 4: 송장 후 처리.

흐름:
  1. verified/completed plan 선택 (탭 3 와 동일 dropdown)
  2. ① 화주별 출고요청 (네뉴=이지어드민 / 캐처스=다원 출고요청서)
  3. ② 다원 송장 채번 → 이지어드민 송장 업로드 양식 생성 (네뉴만)
"""
from __future__ import annotations

import io
from datetime import date as _date

import streamlit as st

from outputs.daone.builder import build_daone_xlsx
from rocketgrowth.db import get_session
from rocketgrowth.models import InboundPlan
from rocketgrowth.secondary_export import (
    build_invoice_upload_form,
    build_order_form,
    build_shipping_bulk_form,
    parse_order_search_file,
)

from channels.rocketgrowth._helpers import section_note
from channels.rocketgrowth._dispatch_helpers import (
    _BRAND_TO_COMPANY, build_dispatch_data, render_context_bar, select_dispatch_plan,
)


# 캐처스 다원 출고요청서 placeholder 정보 (탭 4 로 이전됨)
COUPANG_FC_ADDRESS = {
    '동탄1': '경기 화성시 동탄ㅇㅇ로 (placeholder)',
    '화성2': '경기 화성시 화성ㅇㅇ로 (placeholder)',
    '천안2': '충남 천안시 천안ㅇㅇ로 (placeholder)',
    '옥천3': '충북 옥천군 옥천ㅇㅇ로 (placeholder)',
}
COUPANG_FC_PHONE = '02-1577-7011'
CACHERS_INFO = {
    'name': '캐처스',
    'phone1': '02-0000-0000',
    'phone2': '',
}


def _render_complete_button(brand: str, plan):
    """탭 4 마지막 — 모든 작업 완료 시 status=completed 로 변경."""
    st.divider()
    is_done = (plan.status or "") == "completed"
    if is_done:
        st.button(
            "🏁 완료됨",
            disabled=True, width="stretch",
            key=f"inv_{brand}_complete_done_{plan.id}",
            help=f"plan #{plan.id} status=completed",
        )
    else:
        if st.button(
            "🏁 완료",
            type="primary", width="stretch",
            key=f"inv_{brand}_complete_{plan.id}",
            help="모든 출고 후 처리 작업 완료. 상태 -> completed.",
        ):
            try:
                with get_session() as _cs:
                    _p = _cs.get(InboundPlan, plan.id)
                    _p.status = "completed"
                    _cs.commit()
                st.success(f"🏁 완료 (plan #{plan.id})")
                st.rerun()
            except Exception as ex:
                st.error(f"완료 처리 실패: {ex}")


def _sec_items_to_daone_rows(sec_items, fc_name, brand_company, milkrun_id, arrival_date):
    """SecondaryItem → 다원 19컬럼 dict 리스트 (캐처스 전용)."""
    rows = []
    seq = 0
    for it in sec_items:
        if it.inbound_qty <= 0:
            continue
        seq += 1
        rows.append({
            '몰명(또는 몰코드)': '쿠팡 로켓그로스',
            '출하의뢰번호': f"{milkrun_id}",
            '출하의뢰항번': str(seq),
            '고객주문번호': str(it.coupang_option_id),
            '상품명': it.product_name or '',
            '제품코드': it.own_wms_barcode or '',
            '주문수량': it.inbound_qty,
            '주문자명': CACHERS_INFO['name'],
            '주문자연락처1': CACHERS_INFO['phone1'],
            '주문자연락처2': CACHERS_INFO['phone2'],
            '수취인명': f'쿠팡 {fc_name}',
            '수취인연락처1': COUPANG_FC_PHONE,
            '수취인연락처2': '',
            '수취인우편번호': '',
            '수취인주소1': COUPANG_FC_ADDRESS.get(fc_name, f'쿠팡 {fc_name} (주소 미등록)'),
            '주소2': '',
            '배송메시지': f'쿠팡 로켓그로스 입고 ({arrival_date})' if arrival_date else '쿠팡 로켓그로스 입고',
            '송장번호': '',
            '택배사명': '',
        })
    return rows


def render(brand: str):
    """탭 4 메인."""
    brand_company = _BRAND_TO_COMPANY[brand]

    plan = select_dispatch_plan(brand, brand_company, key_suffix="invoice")
    if plan is None:
        return

    render_context_bar(plan)

    data = build_dispatch_data(brand, brand_company, plan)
    if data is None:
        return

    # 택배는 탭 4 에서 할 일 없음 — 단순 안내 + 완료 버튼만
    if not data.is_milkrun:
        st.info(
            "📦 **택배 출고**: 탭 4 에서 별도 작업 없음. "
            "채번된 송장번호는 Wing에 등록해주세요."
        )
        _render_complete_button(brand, plan)
        return

    # ─── 이하 밀크런 흐름 ─────────────────────────────────
    # ─── ① 이지어드민 발주 (네뉴) / ① 화주별 출고요청 (캐처스=다원) ────
    _is_parcel_now = False  # 위에서 이미 분기됨, 가독성 위해 유지
    if brand == 'nenu':
        st.subheader("① 이지어드민 발주")
        section_note(
            "이지어드민 발주서양식 다운로드 → 이지어드민 수동 발주"
        )
        try:
            order_xlsx = build_order_form(
                data.sec_items, data.fc, str(data.order_base).strip(),
                pallet_assignment=data.pa,
            )
            st.download_button(
                "📥 이지어드민 발주서양식",
                data=order_xlsx,
                file_name=(
                    f"{data.ship_prefix}재고차감_로켓그로스({brand_company}커머스)"
                    f"발주서양식_{data.datesuf}.xlsx"
                ),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch", type="primary",
                key=f"inv_{brand}_dl_eaorder_{plan.id}",
            )
        except Exception as ex:
            st.error(f"이지어드민 발주서 생성 실패: {ex}")
        st.divider()
    else:
        st.subheader(f"① 화주별 출고요청 — {brand_company}")
        section_note(
            "캐처스: 다원 출고요청서.xlsx 다운로드 → 다원에 직접 업로드 (수기). "
            "이지어드민 미사용 (캐처스 ↔ 다원 자동연동 없음)."
        )
        try:
            daone_rows = _sec_items_to_daone_rows(
                data.sec_items, data.fc, brand_company,
                milkrun_id=data.order_base or str(plan.id),
                arrival_date=data.arr,
            )
            if not daone_rows:
                st.info("출고 대상 (inbound_qty > 0) SKU 가 없습니다.")
            else:
                xlsx_bytes = build_daone_xlsx(daone_rows)
                st.download_button(
                    "📥 다원 출고요청서",
                    data=xlsx_bytes,
                    file_name=(
                        f"{data.ship_prefix}_다원출고요청_로켓그로스(캐처스)_{data.fc}_{data.datesuf}.xlsx"
                    ),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch", type="primary",
                    key=f"inv_{brand}_dl_daone_{plan.id}",
                )
                st.caption(
                    "⚠️ 주문자/수취인 정보는 placeholder — 다원 업로드 전 확인 필요."
                )
        except Exception as ex:
            st.error(f"다원 출고요청서 생성 실패: {ex}")

    st.divider()

    # ─── ② 송장 후 처리 (네뉴: 이지어드민 양식 / 캐처스: 안내) ────
    st.subheader("② 이지어드민 송장등록&배송처리")

    # 화주 분기
    if brand == 'cachers':
        section_note(
            "캐처스: 이지어드민 미사용. 다원에서 채번된 송장은 캐처스 자체 시스템에 "
            "직접 등록 필요 (수기). 이 탭에서는 별도 결과물 생성 X."
        )
        st.info(
            "ℹ️ **캐처스 송장 처리 방식**: 다원 채번 파일을 받으면 캐처스 운영팀이 "
            "내부 시스템(또는 직접 출고 채널)에 송장번호 등록. "
            "쿠팡 송장 업로드 양식은 Phase F 후속에서 추가 예정."
        )
        _render_complete_button(brand, plan)
        return

    # 네뉴: 확장주문검색 → 배송일괄처리양식 + 송장업로드양식 생성
    section_note(
        "이지어드민에서 확장주문검색 .xls 다운로드 → 업로드 → "
        "두 양식 (배송일괄처리 / 송장업로드) 자동 생성.<br>"
        "송장번호는 <b>관리번호(=고객주문번호) + '000000'</b> 으로 generate."
    )

    order_file = st.file_uploader(
        "확장주문검색 파일 (.xls)",
        type=['xls'],
        key=f"inv_{brand}_ordersearch_{plan.id}",
        help="이지어드민 확장주문검색에서 다운로드한 .xls 파일 (송장번호 컬럼 비어있어도 OK)",
    )

    if not order_file:
        st.caption("⚠️ 확장주문검색 파일 업로드 대기 중.")
        _render_complete_button(brand, plan)
        return

    try:
        order_rows = parse_order_search_file(order_file.getvalue())
    except Exception as ex:
        st.error(f"확장주문검색 파일 파싱 실패: {ex}")
        return

    if not order_rows:
        st.warning("📭 데이터가 없습니다. 파일 내용 확인 필요.")
        _render_complete_button(brand, plan)
        return

    st.metric("✅ 인식된 행", len(order_rows))

    # 미리보기
    with st.expander(f"미리보기 ({len(order_rows)} 행)", expanded=False):
        import pandas as pd
        prev = pd.DataFrame([{
            '관리번호(고객주문번호)': r.mgmt_no,
            '주문번호(출하의뢰항번)': r.order_no,
            '상품명': r.product_name,
            '바코드': r.barcode,
            '수량': r.qty,
            '송장번호(generate)': f"{r.mgmt_no}000000" if r.mgmt_no else '',
        } for r in order_rows])
        st.dataframe(prev, width="stretch", hide_index=True)

    # 두 양식 생성
    try:
        xlsx_bulk = build_shipping_bulk_form(order_rows)
        xlsx_waybill = build_invoice_upload_form(order_rows)
    except Exception as ex:
        st.error(f"이지어드민 양식 생성 실패: {ex}")
        return

    today_str = _date.today().strftime('%Y%m%d')
    name_bulk = f"이지어드민_배송일괄처리양식_로켓그로스({brand_company})_{today_str}.xlsx"
    name_waybill = f"이지어드민_송장업로드양식_로켓그로스({brand_company})_{today_str}.xlsx"
    dlc1, dlc2 = st.columns(2)
    with dlc1:
        st.download_button(
            f"📥 배송일괄처리양식",
            data=xlsx_bulk,
            file_name=name_bulk,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary", width="stretch",
            key=f"inv_{brand}_dl_bulk_{plan.id}",
            help="1컬럼 (송장번호 = 관리번호+'000000'). 이지어드민 → 배송 일괄처리 양식 업로드.",
        )
    with dlc2:
        st.download_button(
            f"📥 송장업로드양식",
            data=xlsx_waybill,
            file_name=name_waybill,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary", width="stretch",
            key=f"inv_{brand}_dl_waybill_{plan.id}",
            help="택배사 / 송장번호 / 관리번호 (3컬럼). 이지어드민 → 송장 일괄 등록 양식 업로드.",
        )

    _render_complete_button(brand, plan)
