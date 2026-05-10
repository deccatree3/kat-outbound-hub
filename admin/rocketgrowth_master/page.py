"""로켓그로스 마스터 (WmsProduct + CoupangProduct) 관리 페이지.

원본 프로젝트(nn-rocketgrowth_inventory) 의 '상품 정보 관리' 다운로드/업로드
부분을 이전. CRUD 탭은 후속 단계에서 추가 가능.
"""
from __future__ import annotations

from datetime import date as _date
from io import BytesIO

import pandas as pd
import streamlit as st
from sqlalchemy import select

from rocketgrowth.db import get_session
from rocketgrowth.master_io import (
    parse_master_file, upsert_coupang_records, upsert_wms_records,
)
from rocketgrowth.models import CoupangProduct, WmsProduct


def _build_master_xlsx() -> bytes:
    """현재 DB 의 WmsProduct·CoupangProduct 를 마스터 파일 포맷으로 xlsx 생성."""
    import xlsxwriter as _xw

    with get_session() as s:
        wms_rows = s.execute(
            select(WmsProduct).order_by(WmsProduct.wms_barcode)
        ).scalars().all()
        cp_rows = s.execute(
            select(CoupangProduct).order_by(CoupangProduct.coupang_option_id)
        ).scalars().all()

    wms_headers = [
        "WMS바코드", "제품명", "낱개수량", "부모_WMS바코드",
        "1카톤박스입수량", "중량", "소비기한일수", "옵션ID", "부모_옵션ID",
    ]
    cp_headers = [
        "등록상품 ID", "옵션 ID", "SKU ID", "등록상품명", "옵션명",
        "상품등급", "상품등록일", "수동입고여부",
        "WMS바코드", "쿠팡바코드", "WMS바코드-반품",
    ]

    buf = BytesIO()
    wb = _xw.Workbook(buf, {"in_memory": True})
    hdr_fmt = wb.add_format({"bold": True, "bg_color": "#f0f0f0", "border": 1})
    date_fmt = wb.add_format({"num_format": "yyyy-mm-dd"})

    # WMS상품정보
    ws1 = wb.add_worksheet("WMS상품정보")
    for c, h in enumerate(wms_headers):
        ws1.write_string(0, c, h, hdr_fmt)
    for r, w in enumerate(wms_rows, start=1):
        vals = [
            w.wms_barcode, w.product_name, w.unit_qty, w.parent_wms_barcode,
            w.box_qty, w.weight_g, w.shelf_life_days,
            w.coupang_option_id, w.parent_coupang_option_id,
        ]
        for c, v in enumerate(vals):
            if v is None:
                continue
            if isinstance(v, bool):
                ws1.write_boolean(r, c, v)
            elif isinstance(v, (int, float)):
                ws1.write_number(r, c, v)
            else:
                ws1.write_string(r, c, str(v))
    ws1.set_column(0, 0, 18)
    ws1.set_column(1, 1, 40)
    ws1.freeze_panes(1, 0)

    # 쿠팡상품정보
    ws2 = wb.add_worksheet("쿠팡상품정보")
    for c, h in enumerate(cp_headers):
        ws2.write_string(0, c, h, hdr_fmt)
    for r, p in enumerate(cp_rows, start=1):
        for c, v in enumerate([
            p.coupang_product_id, p.coupang_option_id, p.sku_id,
            p.product_name, p.option_name,
            p.grade, p.registered_at, p.milkrun_managed,
            p.wms_barcode, p.coupang_barcode, p.wms_barcode_return,
        ]):
            if v is None:
                continue
            if hasattr(v, "strftime") and not isinstance(v, str):
                try:
                    ws2.write_datetime(r, c, v, date_fmt)
                    continue
                except Exception:
                    ws2.write_string(r, c, str(v))
                    continue
            if isinstance(v, bool):
                ws2.write_boolean(r, c, v)
            elif isinstance(v, (int, float)):
                ws2.write_number(r, c, v)
            else:
                ws2.write_string(r, c, str(v))
    ws2.set_column(3, 3, 40)
    ws2.freeze_panes(1, 0)

    wb.close()
    buf.seek(0)
    return buf.getvalue()


def render_page():
    st.markdown(
        "로켓그로스 **WMS상품(물리 속성)** + **쿠팡상품(옵션/매핑)** 마스터 관리. "
        "다운로드 → 엑셀에서 수정 → 재업로드 흐름."
    )

    # 카운트
    with get_session() as s:
        wms_n = s.execute(select(WmsProduct)).scalars().all()
        cp_n = s.execute(select(CoupangProduct)).scalars().all()
    c1, c2 = st.columns(2)
    c1.metric("WmsProduct (WMS상품)", f"{len(wms_n):,} 건")
    c2.metric("CoupangProduct (쿠팡상품)", f"{len(cp_n):,} 건")

    st.divider()

    # ─── 📥 다운로드 ────────────────────────────────────
    st.subheader("📥 마스터 파일 다운로드")
    st.caption(
        "DB 현재 상태를 두 시트(WMS상품정보 / 쿠팡상품정보) 로 받아 "
        "엑셀에서 수정 후 아래에서 재업로드 가능."
    )
    try:
        master_bytes = _build_master_xlsx()
        st.download_button(
            "📥 현재 마스터 파일 다운로드 (.xlsx)",
            data=master_bytes,
            file_name=f"마스터-상품정보_{_date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
    except Exception as ex:
        st.error(f"마스터 파일 생성 실패: {ex}")

    st.divider()

    # ─── 📤 업로드 (전체 교체) ──────────────────────────
    st.subheader("📤 마스터 파일 업로드 (전체 교체)")
    st.markdown(
        "**마스터-상품정보.xlsx** 와 동일한 형식의 엑셀 파일을 업로드.\n"
        "- **WMS상품정보** 시트: WMS바코드 / 제품명 / 낱개수량 / 부모_WMS바코드 / "
        "1카톤박스입수량 / 중량 / 소비기한일수 / 옵션ID / 부모_옵션ID\n"
        "- **쿠팡상품정보** 시트: 등록상품ID / 옵션ID / SKU ID / 등록상품명 / 옵션명 / "
        "상품등급 / 상품등록일 / 수동입고여부 / WMS바코드 / 쿠팡바코드 / WMS바코드-반품\n\n"
        "두 시트 모두 있으면 양쪽 모두 적용, 한 시트만 있으면 해당 테이블만 적용."
    )

    master_file = st.file_uploader(
        "마스터 파일 업로드 (.xlsx)",
        type=["xlsx"],
        key="rg_master_upload",
        help="업로드 시 **전체 교체** — 파일에 없는 기존 항목은 삭제됩니다.",
    )

    if master_file:
        try:
            parsed = parse_master_file(master_file.getvalue(), master_file.name)
            wms_count = len(parsed["wms"])
            cp_count = len(parsed["coupang"])
            wms_skipped = parsed.get("wms_skipped", [])
            st.success(f"파싱 완료: WMS {wms_count}건 · 쿠팡 {cp_count}건")

            if wms_skipped:
                st.warning(
                    f"⚠️ WMS상품정보 시트에서 WMS바코드 가 비어있어 "
                    f"{len(wms_skipped)}건이 스킵됐습니다."
                )
                with st.expander(f"스킵된 {len(wms_skipped)}건 보기", expanded=False):
                    st.dataframe(
                        pd.DataFrame(wms_skipped),
                        width="stretch", hide_index=True,
                    )

            if wms_count > 0:
                with st.expander(f"WMS {wms_count}건 미리보기", expanded=False):
                    st.dataframe(
                        pd.DataFrame(parsed["wms"]).head(20),
                        width="stretch",
                    )
            if cp_count > 0:
                with st.expander(f"쿠팡 {cp_count}건 미리보기", expanded=False):
                    st.dataframe(
                        pd.DataFrame(parsed["coupang"]).head(20),
                        width="stretch",
                    )

            st.warning(
                "⚠️ **전체 교체 모드**: 파일에 없는 기존 항목은 삭제됩니다. "
                "파일이 마스터의 유일한 원본이 되어야 합니다."
            )

            if st.button("✅ DB에 적용 (전체 교체)", type="primary", key="rg_master_apply"):
                results = []
                if wms_count > 0:
                    s = upsert_wms_records(parsed["wms"], replace_all=True)
                    results.append(
                        f"WMS: +{s['added']} 추가, {s['updated']} 수정, "
                        f"-{s['deleted']} 삭제"
                    )
                if cp_count > 0:
                    s = upsert_coupang_records(parsed["coupang"], replace_all=True)
                    results.append(
                        f"쿠팡: +{s['added']} 추가, {s['updated']} 수정, "
                        f"-{s['deleted']} 삭제"
                    )
                st.success(" · ".join(results))
                st.rerun()
        except Exception as e:
            st.error(f"파일 처리 실패: {e}")
