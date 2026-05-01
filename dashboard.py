"""
KAT Outbound Hub — 캐처스/네뉴 출고 통합 대시보드.

채널별 페이지를 좌측 셀렉터로 디스패치. 각 채널은 channels/<id>/page.py 에서
render_page()를 export.
"""
import os

import streamlit as st


st.set_page_config(
    page_title="KAT Outbound Hub",
    page_icon="📤",
    layout="wide",
)

# Streamlit Cloud secrets → env 변수 승격 (db/pg.py 가 DATABASE_URL 환경변수 우선)
try:
    if hasattr(st, "secrets"):
        for key in ("DATABASE_URL", "QOO10_API_KEY", "QOO10_USER_ID", "QOO10_PASSWORD"):
            if key in st.secrets and not os.environ.get(key):
                os.environ[key] = str(st.secrets[key])
except Exception:
    pass


st.title("📤 KAT Outbound Hub")
st.caption("캐처스/네뉴 출고 통합")

# 채널 레지스트리. status는 사용자에게 표시되는 진행도. 'render'가 있으면 dispatch 가능.
CHANNELS = {
    "qoo10_japan":          {"label": "Qoo10 일본",       "brand": "캐처스",     "status": "✅ 운영"},
    "domestic":             {"label": "국내몰",           "brand": "캐처스/네뉴", "status": "✅ 운영"},
    "cachers_qoo10_kr":     {"label": "캐처스 큐텐 국내", "brand": "캐처스",     "status": "✅ 운영 (SKU 매핑 추후)"},
    "cachers_makers":       {"label": "캐처스 메이커스",  "brand": "캐처스",     "status": "Phase 3"},
    "cachers_rocketgrowth": {"label": "캐처스 로켓그로스", "brand": "캐처스",     "status": "Phase 3 (부착문서 多)"},
    "nenu_telepay":         {"label": "네뉴 텔레페이",    "brand": "네뉴",       "status": "Phase 4"},
}

ADMIN_PAGES = {
    "sku_catalog": {"label": "🗂 KSE SKU 마스터", "desc": "JP/KR 카탈로그 관리"},
}

# ─── Sidebar ───
st.sidebar.title("🚚 출고 작업")
selected = st.sidebar.selectbox(
    "채널 선택",
    options=list(CHANNELS.keys()),
    format_func=lambda k: f"{CHANNELS[k]['label']} ({CHANNELS[k]['brand']})",
)

st.sidebar.markdown("---")
st.sidebar.markdown("**⚙️ 관리**")
admin_selected = st.sidebar.radio(
    "관리 페이지",
    options=['(없음)'] + list(ADMIN_PAGES.keys()),
    format_func=lambda k: '— 채널 페이지 보기 —' if k == '(없음)' else ADMIN_PAGES[k]['label'],
    label_visibility='collapsed',
)

st.sidebar.markdown("---")
st.sidebar.caption("이 프로젝트는 출고 일 전담. 일본 KSE 물류비/재고 분석은 별도 프로젝트(`kat-kse-3pl-japan`).")

# ─── Main 디스패치 ───
if admin_selected != '(없음)':
    page = ADMIN_PAGES[admin_selected]
    st.subheader(page['label'])
    st.caption(page['desc'])
    if admin_selected == 'sku_catalog':
        from admin.sku_catalog.page import render_page
        render_page()
else:
    ch = CHANNELS[selected]
    st.subheader(f"{ch['label']}")
    st.caption(f"화주: {ch['brand']} · 상태: {ch['status']}")

    if selected == "qoo10_japan":
        from channels.qoo10_japan.page import render_page
        render_page()
    elif selected == "domestic":
        from channels.domestic.page import render_page
        render_page()
    elif selected == "cachers_qoo10_kr":
        from channels.cachers_qoo10_kr.page import render_page
        render_page()
    else:
        st.info(
            "📋 이 채널은 아직 구현되지 않았습니다. \n\n"
            "단계별 로드맵은 `CLAUDE.md` 참고."
        )
