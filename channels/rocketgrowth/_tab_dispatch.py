"""탭 3: 물류센터 출고 요청.

흐름:
  1. verified/completed plan 선택
  2. ① 물류센터 전달 파일 (취합리스트, 팔레트적재리스트, 재고이동건, PDF 3종)
"""
from __future__ import annotations

import streamlit as st

from rocketgrowth.db import get_session
from rocketgrowth.models import InboundPlan
from rocketgrowth.secondary_export import (
    build_consolidation_list, build_order_form, build_pallet_loading_list,
    build_parcel_consolidation_list, build_parcel_eza_order_form,
    update_inventory_movement,
)

from channels.rocketgrowth._helpers import get_fc_info, jump_to_tab, section_note
from channels.rocketgrowth._dispatch_helpers import (
    _BRAND_TO_COMPANY, build_dispatch_data, render_context_bar, select_dispatch_plan,
)


def render(brand: str):
    """탭 3 메인."""
    brand_company = _BRAND_TO_COMPANY[brand]

    plan = select_dispatch_plan(brand, brand_company, key_suffix="dispatch")
    if plan is None:
        return

    render_context_bar(plan)

    data = build_dispatch_data(brand, brand_company, plan)
    if data is None:
        return

    # 택배 + 네뉴: ① 이지어드민 수동 발주 / ② 물류센터 전달 파일
    # 그 외 (밀크런 또는 택배+캐처스): ① 물류센터 전달 파일 만
    _show_eza_order_first = (not data.is_milkrun) and (brand == 'nenu')
    _files_section_no = "②" if _show_eza_order_first else "①"

    if _show_eza_order_first:
        st.subheader("① 이지어드민 수동 발주")
        section_note(
            "택배는 다원 자동연동이 없어 이지어드민에 수동 등록 필요.<br>"
            "아래 발주서 다운로드 → 이지어드민 업로드 → 이지어드민↔다원 연동으로 발주 전달.<br>"
            "박스 단위 (각 박스 = 1행, 수령인이 같으면 합포장)."
        )
        # FC 정보 조회 (탭 2 검수 단계에서 입력되어 있어야 함)
        _fc_info = get_fc_info(data.fc) if data.fc else None
        if _fc_info is None:
            st.error(
                f"❌ FC '{data.fc}' 정보가 등록되어 있지 않습니다. "
                "탭 2 검수 단계에서 FC 정보를 먼저 등록해 주세요."
            )
        else:
            try:
                _itr_id = (
                    getattr(data.attachment, 'itr_id', None)
                    or data.order_base or ""
                )
                order_xlsx = build_parcel_eza_order_form(
                    data.sec_items,
                    fc_name=data.fc,
                    fc_address=_fc_info.address,
                    fc_phone=_fc_info.phone,
                    itr_id=_itr_id,
                    sku_order=getattr(data.attachment, 'sku_order', None),
                )
                st.download_button(
                    "📥 이지어드민 발주서양식 (택배)",
                    data=order_xlsx,
                    file_name=(
                        f"{brand_company}_이지어드민 발주서_{data.fc}_{data.datesuf}.xlsx"
                    ),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch", type="primary",
                    key=f"disp_{brand}_dl_eaorder_{plan.id}",
                )
            except Exception as ex:
                st.error(f"이지어드민 발주서 생성 실패: {ex}")
        st.divider()

    st.subheader(f"{_files_section_no} 물류센터 전달 파일 ({data.ship_label})")
    if data.is_milkrun:
        section_note(
            "아래 파일 다운로드 → 메일 송부.<br>"
            "<b>밀크런</b>: 팔레트 단위 → 팔레트적재리스트 포함."
        )
    else:
        section_note(
            "아래 파일 다운로드 → 메일 송부.<br>"
            "<b>택배</b>: 박스 단위 → 팔레트적재리스트 제외 (택배 박스 라벨은 후속 단계에서 추가)."
        )

    fc, arr = data.fc, data.arr
    yymmdd, yyyymm, datesuf = data.yymmdd, data.yyyymm, data.datesuf
    order_base = data.order_base
    ship_prefix = data.ship_prefix
    is_milkrun = data.is_milkrun

    # 취합리스트 + (밀크런만) 팔레트적재 + 재고이동건
    if is_milkrun:
        dc = st.columns(3)
    else:
        dc = st.columns(2)
    try:
        if is_milkrun:
            cons = build_consolidation_list(
                data.sec_items, data.pa, fc, arr, brand_company,
                data.invoice.order_id if data.invoice and data.invoice.order_id else data.attachment.milkrun_id,
            )
        else:
            # 택배: 부착문서 SKU 순서 기반 박스 NO 자동 부여
            cons = build_parcel_consolidation_list(
                data.sec_items, fc, arr, brand_company,
                sku_order=getattr(data.attachment, 'sku_order', None),
            )
        with dc[0]:
            st.download_button(
                "📥 취합리스트", data=cons,
                file_name=f"{brand_company}_{ship_prefix}_취합리스트_{yymmdd}_{fc}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch", type="primary",
                key=f"disp_{brand}_dl_cons_{plan.id}",
            )
    except Exception as ex:
        with dc[0]:
            st.error(f"취합리스트: {ex}")

    from rocketgrowth.config import load_config
    cfg = load_config()
    if is_milkrun:
        try:
            pal = build_pallet_loading_list(
                data.sec_items, data.pa, fc, arr,
                milkrun_request_id=order_base, pallet_size=cfg.pallet_size_boxes,
            )
            with dc[1]:
                st.download_button(
                    "📥 팔레트적재리스트", data=pal,
                    file_name=f"밀크런_물류부착문서2 (팔레트적재리스트)_{fc}_{datesuf}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch", type="primary",
                    key=f"disp_{brand}_dl_pal_{plan.id}",
                )
        except Exception as ex:
            with dc[1]:
                st.error(f"팔레트적재: {ex}")
        mv_col = dc[2]
    else:
        mv_col = dc[1]

    if plan.movement_template_blob:
        try:
            mv_out = update_inventory_movement(
                bytes(plan.movement_template_blob), data.sec_items, arr, fc, brand_company,
            )
            with mv_col:
                st.download_button(
                    "📥 재고이동건", data=mv_out,
                    file_name=plan.movement_template_filename or f"쿠팡 재고이동건_{yyyymm}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch", type="primary",
                    key=f"disp_{brand}_dl_mv_{plan.id}",
                )
        except Exception as ex:
            with mv_col:
                st.error(f"재고이동건: {ex}")
    else:
        with mv_col:
            st.caption("재고이동건 템플릿 미저장 — 탭 1 에서 업로드 시 활성화")

    # PDF 리네임 다운로드 (운송별 명칭 차이)
    dpc = st.columns(3)
    if data.invoice_bytes:
        with dpc[0]:
            st.download_button(
                "📥 물류동봉문서(거래명세서)", data=data.invoice_bytes,
                file_name=f"{ship_prefix}_물류동봉문서(거래명세서)_{fc}_{datesuf}.pdf",
                mime="application/pdf", width="stretch", type="primary",
                key=f"disp_{brand}_dl_inv_{plan.id}",
            )
    else:
        with dpc[0]:
            if not is_milkrun:
                st.caption("동봉문서 N/A (택배 + 혼적 박스 없음)")
            else:
                st.warning("⚠️ 밀크런 — 동봉문서 누락 (필수)")
    with dpc[1]:
        st.download_button(
            "📥 제품 바코드라벨", data=data.label_bytes,
            file_name=f"제품 바코드라벨_{fc}_{datesuf}.pdf",
            mime="application/pdf", width="stretch", type="primary",
            key=f"disp_{brand}_dl_lb_{plan.id}",
        )
    with dpc[2]:
        if is_milkrun:
            attach_filename = f"밀크런_물류부착문서1 (팔레트부착문서)_{fc}_{datesuf}.pdf"
            attach_label = "팔레트부착"
        else:
            # 택배: 쉽먼트_물류부착문서_{FC}_{date}.pdf
            attach_filename = f"쉽먼트_물류부착문서_{fc}_{datesuf}.pdf"
            attach_label = "박스부착"
        st.download_button(
            f"📥 물류부착문서({attach_label})", data=data.attach_bytes,
            file_name=attach_filename,
            mime="application/pdf", width="stretch", type="primary",
            key=f"disp_{brand}_dl_ab_{plan.id}",
        )

    if not is_milkrun:
        st.caption(
            "📦 택배: 박스 NO 는 부착문서 SKU 나열 순서 기준 자동 부여 "
            "(같은 SKU 내 수량 적은 박스가 먼저)."
        )

    # 출고요청 확정 + 다음 단계
    st.divider()
    _already_verified = (plan.status or "") in ("verified", "completed")
    btm_cols = st.columns(2)
    with btm_cols[0]:
        if _already_verified:
            st.button(
                "✅ 출고요청 확정됨",
                disabled=True, width="stretch",
                key=f"disp_{brand}_verify_done_{plan.id}",
                help=f"plan #{plan.id} status={plan.status}",
            )
        else:
            if st.button(
                "출고요청 확정",
                type="primary", width="stretch",
                key=f"disp_{brand}_verify_{plan.id}",
                help="물류센터에 출고 요청 완료. 상태 -> verified (발주확정).",
            ):
                try:
                    with get_session() as _vs:
                        _p = _vs.get(InboundPlan, plan.id)
                        _p.status = "verified"
                        _vs.commit()
                    st.success(f"✅ 출고요청 확정 완료 (plan #{plan.id})")
                    st.rerun()
                except Exception as ex:
                    st.error(f"확정 실패: {ex}")
    with btm_cols[1]:
        if st.button(
            "다음 단계 →",
            key=f"disp_{brand}_goto_invoice_{plan.id}",
            type="primary" if _already_verified else "secondary",
            disabled=(not _already_verified),
            width="stretch",
            help="출고 후 처리 탭으로 자동 이동 (출고요청 확정 후 활성).",
        ):
            st.session_state[f"rg_{brand}_pending_invoice_pick"] = plan.id
            jump_to_tab(3)
