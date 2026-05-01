"""
KAT Outbound Hub — 캐처스/네뉴 출고 통합 대시보드 (Phase 0 스켈레톤).

다음 단계: channels/qoo10_japan, outputs/kse_japan 구현 (Phase 1).
"""
import streamlit as st


st.set_page_config(
    page_title="KAT Outbound Hub",
    page_icon="📤",
    layout="wide",
)

st.title("📤 KAT Outbound Hub")
st.caption("캐처스/네뉴 출고 통합 — Phase 0 스켈레톤")

# 채널 레지스트리 (Phase 1+에서 실제 어댑터로 채워짐)
CHANNELS = {
    "qoo10_japan": {"label": "Qoo10 일본", "brand": "캐처스", "status": "Phase 1 이전 예정"},
    "cachers_domestic": {"label": "캐처스 국내몰", "brand": "캐처스", "status": "MVP (Phase 2)"},
    "cachers_qoo10_kr": {"label": "캐처스 큐텐 국내", "brand": "캐처스", "status": "Phase 3"},
    "cachers_makers": {"label": "캐처스 메이커스", "brand": "캐처스", "status": "Phase 3"},
    "cachers_rocketgrowth": {"label": "캐처스 로켓그로스", "brand": "캐처스", "status": "Phase 3 (부착문서 多)"},
    "nenu_telepay": {"label": "네뉴 텔레페이", "brand": "네뉴", "status": "Phase 4"},
    # ... CLAUDE.md 24개 채널 참고
}

# ─── Sidebar ───
st.sidebar.title("🚚 출고 작업")
selected = st.sidebar.selectbox(
    "채널 선택",
    options=list(CHANNELS.keys()),
    format_func=lambda k: f"{CHANNELS[k]['label']} ({CHANNELS[k]['brand']})",
)

st.sidebar.markdown("---")
st.sidebar.caption("이 프로젝트는 출고 일 전담. 일본 KSE 물류비/재고 분석은 별도 프로젝트(`kat-kse-3pl-japan`).")

# ─── Main ───
ch = CHANNELS[selected]
st.subheader(f"{ch['label']}")
st.caption(f"화주: {ch['brand']} · 상태: {ch['status']}")

st.info(
    "📋 **Phase 0 — 골격만 구축됨**. \n\n"
    "다음 작업 (새 Claude Code 세션에서):\n"
    "1. Phase 1: Qoo10 일본 이전 (`kat-kse-3pl-japan`에서 가져옴)\n"
    "2. Phase 2: MVP — 캐처스 국내몰 다원 발주서 빌더\n\n"
    "자세한 계획은 `CLAUDE.md` 참고."
)
