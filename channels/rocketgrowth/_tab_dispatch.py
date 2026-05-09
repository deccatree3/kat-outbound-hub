"""탭 3: 물류센터 출고 요청.

흐름:
  1. verified/completed plan 선택
  2. ① 물류센터 전달 파일 (취합리스트, 팔레트적재리스트, 재고이동건, PDF 3종)
"""
from __future__ import annotations

import streamlit as st

from rocketgrowth.secondary_export import (
    build_consolidation_list, build_pallet_loading_list,
    update_inventory_movement,
)

from channels.rocketgrowth._helpers import section_note
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

    st.subheader(f"① 물류센터 전달 파일 ({data.ship_label})")
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
        cons = build_consolidation_list(
            data.sec_items, data.pa, fc, arr, brand_company,
            data.invoice.order_id if data.invoice and data.invoice.order_id else data.attachment.milkrun_id,
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
            st.caption("동봉문서 미업로드 (혼적 박스 없는 경우)")
    with dpc[1]:
        st.download_button(
            "📥 제품 바코드라벨", data=data.label_bytes,
            file_name=f"제품 바코드라벨_{fc}_{datesuf}.pdf",
            mime="application/pdf", width="stretch", type="primary",
            key=f"disp_{brand}_dl_lb_{plan.id}",
        )
    with dpc[2]:
        attach_label = "팔레트부착" if is_milkrun else "박스부착"
        st.download_button(
            f"📥 물류부착문서({attach_label})", data=data.attach_bytes,
            file_name=f"{ship_prefix}_물류부착문서1 ({attach_label}문서)_{fc}_{datesuf}.pdf",
            mime="application/pdf", width="stretch", type="primary",
            key=f"disp_{brand}_dl_ab_{plan.id}",
        )

    if not is_milkrun:
        st.info("📦 택배 박스 라벨 출력 양식은 후속 단계에서 추가 예정.")

    # 다음 단계 (송장 후처리 탭으로 이동)
    st.divider()
    import streamlit.components.v1 as components
    if st.button(
        "다음 단계 →",
        key=f"disp_{brand}_goto_invoice_{plan.id}",
        type="primary",
        width="stretch",
        help="송장 후처리 탭으로 자동 이동.",
    ):
        st.session_state[f"rg_{brand}_pending_invoice_pick"] = plan.id
        # tab index: 0=발주계획, 1=쿠팡입고생성, 2=물류센터출고요청, 3=송장후처리
        components.html(
            """
            <script>
            const tabs = window.parent.document.querySelectorAll('button[role="tab"]');
            if (tabs.length > 3) {
                tabs[3].click();
                window.parent.scrollTo({top: 0, behavior: 'smooth'});
            }
            </script>
            """,
            height=0,
        )
