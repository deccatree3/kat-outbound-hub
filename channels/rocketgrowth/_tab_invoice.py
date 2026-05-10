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

from outputs.eza.builder import (
    EZA_WAYBILL_DEFAULT_CARRIER,
    parse_daone_invoice_xls,
    build_eza_waybill_from_triples,
)
from outputs.daone.builder import build_daone_xlsx
from rocketgrowth.db import get_session
from rocketgrowth.models import InboundPlan
from rocketgrowth.secondary_export import build_order_form

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

    # ─── ① 화주별 출고요청 (네뉴=이지어드민 / 캐처스=다원) ────
    # 단, 네뉴+택배는 탭 3 ① 이지어드민 수동 발주 로 이동 — 안내만 표시
    _is_parcel_now = not data.is_milkrun
    if brand == 'nenu' and _is_parcel_now:
        st.info(
            "ℹ️ 택배 + 네뉴 — 이지어드민 발주서는 **탭 3 (물류센터 출고 요청) ① 이지어드민 수동 발주** "
            "에서 다운로드해 주세요. 이 탭은 ② 다원 송장 채번 만 진행."
        )
        st.divider()
        # ② 가 아닌 그대로 ② 다원 송장 채번 으로 진행 (아래 코드 변동 없음)
    elif brand == 'nenu':
        st.subheader(f"① 화주별 출고요청 — {brand_company}")
        section_note(
            "네뉴(서현커머스): 이지어드민 발주서양식 다운로드 → 이지어드민 업로드 → "
            "이지어드민↔다원 자동연동으로 다원에 발주 전달 (재고차감)."
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
    st.subheader("② 다원 송장 채번 → 이지어드민 송장 양식")

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

    # 네뉴: 이지어드민 송장 양식 생성
    section_note(
        "다원에서 채번된 송장 파일 (.xls) 업로드 → 이지어드민 송장 업로드 양식 자동 생성. "
        "이지어드민에 업로드하면 EZA↔다원 자동연동으로 쿠팡까지 흐름."
    )

    daone_file = st.file_uploader(
        "다원 채번 파일 (.xls)",
        type=['xls'],
        key=f"inv_{brand}_daone_{plan.id}",
        help="다원에서 송장번호 채번해서 보내주는 .xls 파일",
    )

    if not daone_file:
        st.caption("⚠️ 다원 채번 파일 업로드 대기 중.")
        _render_complete_button(brand, plan)
        return

    # 택배사 입력 (default = CJ대한통운)
    cols = st.columns([2, 1])
    with cols[0]:
        carrier = st.text_input(
            "택배사",
            value=EZA_WAYBILL_DEFAULT_CARRIER,
            key=f"inv_{brand}_carrier_{plan.id}",
            help="이지어드민 송장 양식의 A 컬럼에 채울 택배사명",
        )
    with cols[1]:
        st.caption("기본: CJ대한통운")

    try:
        triples, skipped = parse_daone_invoice_xls(
            daone_file.getvalue(),
            default_carrier=carrier or EZA_WAYBILL_DEFAULT_CARRIER,
        )
    except Exception as ex:
        st.error(f"다원 채번 파일 파싱 실패: {ex}")
        return

    if not triples:
        st.warning("📭 채번된 송장이 없습니다. 파일 내용 확인 필요.")
        return

    # 메트릭
    mc1, mc2 = st.columns(2)
    mc1.metric("✅ 송장 기입", len(triples))
    mc2.metric("⏭ 스킵 (송장/주문 빈값)", skipped,
               help="다원 채번 파일에서 송장번호 또는 주문번호가 빈 행")

    # 미리보기
    with st.expander(f"미리보기 ({len(triples)} 행)", expanded=False):
        import pandas as pd
        st.dataframe(
            pd.DataFrame(triples, columns=['택배사', '송장번호', '관리번호(주문번호)']),
            width="stretch", hide_index=True,
        )

    # 이지어드민 송장 양식 생성
    try:
        xlsx_bytes = build_eza_waybill_from_triples(triples)
    except Exception as ex:
        st.error(f"이지어드민 송장 양식 생성 실패: {ex}")
        return

    today_str = _date.today().strftime('%Y%m%d')
    out_name = f"이지어드민_송장업로드양식_로켓그로스({brand_company})_{today_str}.xlsx"
    st.download_button(
        f"📥 {out_name}",
        data=xlsx_bytes,
        file_name=out_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", width="stretch",
        key=f"inv_{brand}_dl_eza_{plan.id}",
    )
    st.caption("📤 이지어드민 → 송장 일괄 등록 양식으로 업로드.")

    st.info(
        "🚧 **쿠팡 송장 업로드**: 쿠팡 Wing 의 파일 업로드 방식 확인 후 "
        "Phase F 후속 단계에서 양식 추가 예정."
    )

    _render_complete_button(brand, plan)
