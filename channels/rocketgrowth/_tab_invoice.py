"""탭 3: 송장 후처리.

흐름:
  1. plan 선택 (탭 2 와 동일 dropdown)
  2. 다원에서 채번한 송장 파일 업로드 (.xls)
  3. 화주 분기:
     - 네뉴: 이지어드민 송장 업로드 양식.xlsx 생성
     - 캐처스: 수기 처리 안내 (이지어드민 미사용)
  4. 쿠팡 송장 업로드: 일단 보류 (Phase F 이후 검토)
"""
from __future__ import annotations

import io
from datetime import date as _date

import streamlit as st
from sqlalchemy import desc, select

from rocketgrowth.db import get_session
from rocketgrowth.models import InboundPlan
from outputs.eza.builder import (
    EZA_WAYBILL_DEFAULT_CARRIER,
    parse_daone_invoice_xls,
    build_eza_waybill_from_triples,
)

from channels.rocketgrowth._helpers import STATUS_LABELS, section_note


_BRAND_TO_COMPANY = {
    'nenu':    '서현',
    'cachers': '캐처스',
}


def _select_plan(brand_company: str) -> InboundPlan | None:
    """업체별 plan dropdown — 발주확정(verified) 또는 완료(completed) 만 표시."""
    with get_session() as s:
        plans = s.execute(
            select(InboundPlan)
            .where(
                InboundPlan.company_name == brand_company,
                InboundPlan.status.in_(['verified', 'completed']),
            )
            .order_by(desc(InboundPlan.arrival_date), desc(InboundPlan.created_at))
        ).scalars().all()

    if not plans:
        st.info(
            f"📭 **{brand_company}** 의 발주확정(verified)된 plan 이 없습니다. "
            "탭 2 에서 검수 + 발주 확정 먼저 진행."
        )
        return None

    options = [
        f"#{p.id} {STATUS_LABELS.get(p.status, p.status)} · "
        f"{p.arrival_date or p.plan_date or ''}"
        + (f" · {p.fc_name}" if p.fc_name else "")
        + (f" · {p.shipment_type}" if p.shipment_type else "")
        for p in plans
    ]
    sel = st.selectbox(
        "발주 계획 선택 (verified/completed 만)",
        options=range(len(plans)),
        format_func=lambda i: options[i],
        key=f"inv_{brand_company}_plan_select",
    )
    return plans[sel]


def render(brand: str):
    """탭 3 메인."""
    brand_company = _BRAND_TO_COMPANY[brand]

    plan = _select_plan(brand_company)
    if plan is None:
        return

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
