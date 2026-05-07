"""탭 1: 발주 계획 (자매 페이지 lines 401-1374 의 신규 계획 모드 이전).

C-1 (현재): 파일 업로드 + 자동 분류 + 파싱 + 마스터 로드
C-2 (다음): 발주 계획 산출 + 편집 UI
C-3 (다음): 저장 + 쿠팡 양식 다운로드
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import select

from rocketgrowth.config import load_config
from rocketgrowth.db import get_session
from rocketgrowth.file_classifier import (
    FILE_TYPE_COUPANG, FILE_TYPE_WMS, FILE_TYPE_TEMPLATE, FILE_TYPE_MOVEMENT,
    FILE_TYPE_LABELS, classify_uploaded_files,
)
from rocketgrowth.export import extract_template_option_ids
from rocketgrowth.ingestion.coupang_file import parse_coupang_inventory_file
from rocketgrowth.ingestion.wms_file import aggregate_wms_by_barcode, parse_wms_inventory_file
from rocketgrowth.models import CoupangProduct, WmsProduct
from rocketgrowth.planning import PlanParams

from channels.rocketgrowth._helpers import section_note


_BRAND_TO_COMPANY = {
    'nenu':    '서현',
    'cachers': '캐처스',
}


_UPLOAD_GUIDE_ROWS = [
    ("WMS 재고 파일", FILE_TYPE_WMS,
     "다원WMS > 재고관리 > 창고별로케이션별재고(OWNER) > [품목-정상,불량-로케이션-로트] 탭 > 검색 > 우클릭, Export(Excel)",
     "Document_YYYY-MM-DD.xls"),
    ("쿠팡 재고 파일", FILE_TYPE_COUPANG,
     "쿠팡Wing > 로켓그로스 > 재고현황 > 엑셀 다운로드",
     "inventory_health_sku_info_YYYYMMDDhhmmss.xlsx"),
    ("쿠팡 입고생성 파일", FILE_TYPE_TEMPLATE,
     "쿠팡Wing > 로켓그로스 > 입고관리 > 새로운 입고 생성 > 엑셀 다운로드",
     "generated_excel.xlsx"),
    ("재고이동 파일", FILE_TYPE_MOVEMENT,
     "이번달 '쿠팡 재고이동건' 파일",
     "쿠팡 재고이동건_YYYY_MM월.xlsx"),
]
_GUIDE_NA_BY_COMPANY = {("캐처스", FILE_TYPE_MOVEMENT)}


def _render_upload_guide(brand_company: str, group=None) -> str:
    """단일 업체 기준 업로드 가이드 HTML."""
    body = ""
    for label, ft, path, fname_example in _UPLOAD_GUIDE_ROWS:
        if (brand_company, ft) in _GUIDE_NA_BY_COMPANY:
            mark_cell = (
                '<td style="width:60px; text-align:center; '
                'background-color:#eee; color:#888;">—</td>'
            )
        else:
            mark = "✅" if group and ft in group.files else ""
            mark_cell = f'<td style="width:60px; text-align:center;">{mark}</td>'
        body += (
            f"<tr><td>{label}</td>"
            f'<td><code style="font-size:0.85em;">{fname_example}</code></td>'
            f"<td>{path}</td>{mark_cell}</tr>"
        )
    return (
        '<table style="border-collapse: collapse; width: 100%;">'
        '<thead><tr>'
        '<th style="text-align:left;">구분</th>'
        '<th style="text-align:left;">파일명 예시</th>'
        '<th style="text-align:left;">취합 경로</th>'
        f'<th style="width:60px; text-align:center;">{brand_company}</th>'
        '</tr></thead>'
        f'<tbody>{body}</tbody>'
        '</table>'
    )


@st.cache_data(show_spinner="쿠팡 재고 파싱 중...")
def _parse_cp_cached(data: bytes, name: str):
    tmp = Path("./_tmp_cp_" + name)
    tmp.write_bytes(data)
    try:
        return parse_coupang_inventory_file(tmp)
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


@st.cache_data(show_spinner="WMS 재고 파싱 중...")
def _parse_wms_cached(data: bytes, name: str):
    tmp = Path("./_tmp_wms_" + name)
    tmp.write_bytes(data)
    try:
        return parse_wms_inventory_file(tmp)
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


def _extract_template_option_ids(template_file) -> set[int]:
    tmp = Path("./_tmp_tpl_" + template_file.name)
    tmp.write_bytes(template_file.getvalue())
    try:
        return extract_template_option_ids(tmp)
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


def render(brand: str):
    """탭 1 메인 진입점.

    brand: 'nenu' (=서현) | 'cachers'
    """
    cfg = load_config()
    brand_company = _BRAND_TO_COMPANY[brand]

    plan_params = PlanParams(
        lead_time_days=cfg.lead_time_days,
        target_cover_days=cfg.target_cover_days,
        velocity_alpha=cfg.velocity_alpha,
        overstock_days=cfg.overstock_days,
    )
    st.info(
        f"📐 **계산 파라미터**: 리드타임 **{plan_params.lead_time_days}일** · "
        f"목표 커버 **{plan_params.target_cover_days}일** · "
        f"판매속도 블렌딩 α={plan_params.velocity_alpha:.2f} (7일 평균 가중) · "
        f"과잉 경고 {plan_params.overstock_days}일 초과",
        icon="ℹ️",
    )

    st.markdown("##### 1-1 기초자료 업로드")
    section_note("입고수량확정에 필요한 기초 자료를 취합하여 업로드해 주세요.")

    _guide_ph = st.empty()
    _guide_ph.markdown(_render_upload_guide(brand_company, None), unsafe_allow_html=True)

    uploaded_files = st.file_uploader(
        f"파일 업로드 — {brand_company}",
        type=["xlsx", "xls"],
        accept_multiple_files=True,
        key=f"rg_{brand}_upload",
        label_visibility="collapsed",
    )

    if not uploaded_files:
        st.info(f"파일을 업로드하세요. {brand_company} 의 파일 4종을 한 번에 올릴 수 있습니다.")
        return

    # 자동 분류
    classified, company_groups = classify_uploaded_files(uploaded_files)

    # 화주 강제 — 사용자 선택한 채널의 화주만 사용
    group = company_groups.get(brand_company)
    _guide_ph.markdown(
        _render_upload_guide(brand_company, group), unsafe_allow_html=True
    )

    if group is None:
        st.error(
            f"**{brand_company}** 업체로 식별된 파일이 없습니다. "
            "상품 정보 관리에 해당 업체 상품이 등록되어 있는지 확인하세요."
        )
        # 미분류 파일 경고
        unclassified = [cf for cf in classified if not cf.company]
        if unclassified:
            st.warning(
                f"⚠️ {len(unclassified)}개 파일의 업체를 식별 못 했습니다: "
                + ", ".join(cf.file.name for cf in unclassified)
            )
        return

    # 업로드된 파일 중 다른 업체 분류 발견 시 안내 (이 채널에서는 무시)
    other_brands = [c for c in company_groups if c != brand_company]
    if other_brands:
        st.warning(
            f"이 채널은 **{brand_company}** 전용입니다. 다른 업체로 분류된 파일은 무시됩니다: "
            f"{', '.join(other_brands)}"
        )

    # 필수 파일 체크 — 캐처스는 재고이동건 선택사항
    coupang_file = group.files.get(FILE_TYPE_COUPANG)
    wms_file = group.files.get(FILE_TYPE_WMS)
    template_file = group.files.get(FILE_TYPE_TEMPLATE)
    movement_file = group.files.get(FILE_TYPE_MOVEMENT)

    optional_for_company = (
        {FILE_TYPE_MOVEMENT} if brand_company == "캐처스" else set()
    )
    missing_required = [
        ft for ft in group.missing_types if ft not in optional_for_company
    ]
    if missing_required:
        labels = [FILE_TYPE_LABELS[ft] for ft in missing_required]
        st.info(f"**{brand_company}** 미감지 파일: {', '.join(labels)}")

    required_ok = (
        coupang_file and wms_file and template_file
        and (movement_file or FILE_TYPE_MOVEMENT in optional_for_company)
    )
    if not required_ok:
        need = 3 if FILE_TYPE_MOVEMENT in optional_for_company else 4
        st.warning(f"**{brand_company}** 의 필수 파일 {need}종이 모두 필요합니다.")
        return

    # 파싱
    try:
        cp_snap = _parse_cp_cached(coupang_file.getvalue(), coupang_file.name)
        wms_snap = _parse_wms_cached(wms_file.getvalue(), wms_file.name)
        wms_agg = aggregate_wms_by_barcode(wms_snap)
        tpl_option_ids = _extract_template_option_ids(template_file)
    except Exception as ex:
        st.error(f"파일 파싱 실패: {ex}")
        return

    cp_total_before = len(cp_snap.rows)
    if tpl_option_ids:
        cp_snap.rows = [
            r for r in cp_snap.rows if r.coupang_option_id in tpl_option_ids
        ]

    st.success(
        f"✅ {brand_company} 파일 업로드 완료 — "
        f"쿠팡 재고 {len(cp_snap.rows)}/{cp_total_before} 행 (입고생성 양식 옵션 ID 필터), "
        f"WMS {len(wms_snap.rows)} 행, "
        f"WMS 바코드 합산 {len(wms_agg)} 종"
    )

    # 마스터 로드
    with get_session() as session:
        cp_masters = session.execute(
            select(CoupangProduct).where(CoupangProduct.company_name == brand_company)
        ).scalars().all()
        wms_masters = session.execute(
            select(WmsProduct).where(WmsProduct.company_name == brand_company)
        ).scalars().all()

    st.caption(
        f"📚 **{brand_company}** 마스터: 쿠팡 상품 {len(cp_masters)}개 · "
        f"WMS 상품 {len(wms_masters)}개"
    )

    # session 에 결과 저장 — C-2 에서 발주 산출 시 사용
    st.session_state[f'rg_{brand}_cp_snap'] = cp_snap
    st.session_state[f'rg_{brand}_wms_snap'] = wms_snap
    st.session_state[f'rg_{brand}_wms_agg'] = wms_agg
    st.session_state[f'rg_{brand}_movement_file'] = movement_file

    st.info(
        "🚧 **C-1 완료**: 파일 업로드/파싱/마스터 로드 OK.  \n"
        "**C-2 (다음 단계)**: 발주 계획 산출 + 사용자 편집 UI 이전 예정.  \n"
        "**C-3**: 저장 + 쿠팡 업로드 양식 다운로드 이전 예정."
    )
