"""탭 3: 물류센터 출고 요청.

흐름:
  1. verified/completed plan 선택
  2. ① 물류센터 전달 파일 (취합리스트, 팔레트적재리스트, 재고이동건, PDF 3종)
     + 📦 ZIP 일괄 다운로드
     + ⚡ 개별 N개 동시 트리거 (JS multi-download)
"""
from __future__ import annotations

import base64
import io
import json
import zipfile

import streamlit as st
import streamlit.components.v1 as components


def _build_logistics_zip(items: list[tuple[str, bytes]], folder: str = "") -> bytes:
    """파일 (filename, bytes) 리스트를 ZIP 으로 묶음. folder 지정 시 ZIP 내부에 폴더 생성."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fname, content in items:
            if not content:
                continue
            arcname = f"{folder.rstrip('/')}/{fname}" if folder else fname
            zf.writestr(arcname, content)
    return buf.getvalue()


def _render_zip_download_blue(zip_bytes: bytes, fname: str, label: str, key: str):
    """ZIP 다운로드를 파란색 커스텀 버튼으로 렌더 (Streamlit primary=빨강, secondary=회색 외)."""
    b64 = base64.b64encode(zip_bytes).decode('ascii')
    html = f"""
<a href="data:application/zip;base64,{b64}" download="{fname}" id="zip-dl-{key}"
   style="
     display:inline-block; width:100%; padding:0.5rem 1rem;
     background:#1976d2; color:white; border:1px solid #1976d2; border-radius:0.5rem;
     text-align:center; font-weight:600; font-size:14px;
     text-decoration:none; cursor:pointer; box-sizing:border-box;
   ">{label}</a>
"""
    components.html(html, height=50)


def _render_multi_download_trigger(items: list[tuple[str, bytes]], label: str, key: str):
    """한 번의 클릭으로 모든 파일을 개별 다운로드 트리거. base64 + JS."""
    files_js = []
    for name, content in items:
        if not content:
            continue
        b64 = base64.b64encode(content).decode('ascii')
        files_js.append({'name': name, 'b64': b64})
    if not files_js:
        return
    files_json = json.dumps(files_js)
    button_id = f"multi-dl-{key}"
    html = f"""
<button id="{button_id}" style="
    width:100%; padding:0.5rem 1rem;
    background:#ff4b4b; color:white; border:none; border-radius:0.5rem;
    font-weight:600; font-size:14px; cursor:pointer;
">{label}</button>
<script>
(function() {{
    const files = {files_json};
    document.getElementById("{button_id}").onclick = function() {{
        files.forEach((f, i) => {{
            setTimeout(() => {{
                const a = document.createElement('a');
                a.href = 'data:application/octet-stream;base64,' + f.b64;
                a.download = f.name;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
            }}, i * 250);
        }});
    }};
}})();
</script>
"""
    components.html(html, height=50)

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

    # 일괄 다운로드 (ZIP / multi-trigger) 용 — 각 파일 생성 성공 시 append.
    zip_items: list[tuple[str, bytes]] = []

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
                brand=brand,
            )
        cons_name = f"{brand_company}_{ship_prefix}_취합리스트_{yymmdd}_{fc}.xlsx"
        zip_items.append((cons_name, cons))
        with dc[0]:
            st.download_button(
                "📥 취합리스트", data=cons,
                file_name=cons_name,
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
            pal_name = f"밀크런_물류부착문서2 (팔레트적재리스트)_{fc}_{datesuf}.xlsx"
            zip_items.append((pal_name, pal))
            with dc[1]:
                st.download_button(
                    "📥 팔레트적재리스트", data=pal,
                    file_name=pal_name,
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
            mv_name = plan.movement_template_filename or f"쿠팡 재고이동건_{yyyymm}.xlsx"
            zip_items.append((mv_name, mv_out))
            with mv_col:
                st.download_button(
                    "📥 재고이동건", data=mv_out,
                    file_name=mv_name,
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
        inv_name = f"{ship_prefix}_물류동봉문서(거래명세서)_{fc}_{datesuf}.pdf"
        zip_items.append((inv_name, data.invoice_bytes))
        with dpc[0]:
            st.download_button(
                "📥 물류동봉문서(거래명세서)", data=data.invoice_bytes,
                file_name=inv_name,
                mime="application/pdf", width="stretch", type="primary",
                key=f"disp_{brand}_dl_inv_{plan.id}",
            )
    else:
        with dpc[0]:
            if not is_milkrun:
                st.caption("동봉문서 N/A (택배 + 혼적 박스 없음)")
            else:
                st.warning("⚠️ 밀크런 — 동봉문서 누락 (필수)")
    lb_name = f"제품 바코드라벨_{fc}_{datesuf}.pdf"
    zip_items.append((lb_name, data.label_bytes))
    with dpc[1]:
        st.download_button(
            "📥 제품 바코드라벨", data=data.label_bytes,
            file_name=lb_name,
            mime="application/pdf", width="stretch", type="primary",
            key=f"disp_{brand}_dl_lb_{plan.id}",
        )
    with dpc[2]:
        if is_milkrun:
            attach_filename = f"밀크런_물류부착문서1 (팔레트부착문서)_{fc}_{datesuf}.pdf"
            attach_label = "팔레트부착"
        else:
            attach_filename = f"쉽먼트_물류부착문서_{fc}_{datesuf}.pdf"
            attach_label = "박스부착"
        zip_items.append((attach_filename, data.attach_bytes))
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

    # ─── 일괄 다운로드 옵션 ─────
    valid_zip_items = [(n, b) for n, b in zip_items if b]
    if valid_zip_items:
        st.markdown("---")
        st.caption(
            f"📦 일괄 다운로드 옵션 — 위 {len(valid_zip_items)}개 파일 한 번에:"
        )
        bc1, bc2 = st.columns(2)
        with bc1:
            zip_folder = f"{brand_company}_{ship_prefix}_{fc}_{datesuf}"
            try:
                zip_bytes = _build_logistics_zip(valid_zip_items, folder=zip_folder)
                _render_zip_download_blue(
                    zip_bytes=zip_bytes,
                    fname=f"{zip_folder}.zip",
                    label=f"📦 ZIP 다운로드 ({len(valid_zip_items)}개 → 1 파일)",
                    key=f"{brand}_{plan.id}",
                )
            except Exception as ex:
                st.error(f"ZIP 생성 실패: {ex}")
        with bc2:
            _render_multi_download_trigger(
                valid_zip_items,
                label=f"⚡ 개별 {len(valid_zip_items)}개 동시 다운로드",
                key=f"{brand}_{plan.id}",
            )
        st.caption(
            "ZIP = 1개 파일로 전달, 압축 해제 필요. "
            "⚡ 개별 = 압축 없이 N개 파일 그대로 (브라우저가 다중 다운로드 허용 요청할 수 있음)."
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
