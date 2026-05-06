"""
[캐처스] Qoo10 — 탭 1 신규주문 처리.

흐름:
  1. QSM API 로 신규주문 가져오기 (또는 CSV 업로드)
  2. 매핑 활성여부 lookup 으로 자동 분류:
       - JP 활성 매핑 있음 → JP 그룹
       - KR 활성 매핑 있음 → KR 그룹
       - 둘 다 없음 → 미매핑 (어드민 등록 안내)
  3. KR 그룹: 'KR 배송준비 전환' 버튼 → SetSellerCheckYN_V2 호출 (Phase C-4)
  4. JP 그룹: 일본 출고 탭으로 진행
"""
import streamlit as st
import pandas as pd

from db import mapping as _m
from qoo10 import api_client as qapi
from qoo10 import generator as qgen
from utils.timezone import kst_today


CHANNEL_JP = 'qoo10_japan'
CHANNEL_KR = 'cachers_qoo10_kr'


def _classify(qsm_rows, jp_map, kr_map):
    """QSM dict 행들 → JP/KR/미매핑 분류.
    같은 (상품명, 옵션) 이 양쪽 모두 활성이면 KR 우선 (warning 표시).
    """
    jp_orders = []
    kr_orders = []
    unknown_orders = []
    conflict_keys = set()

    for q in qsm_rows:
        name = (q.get('상품명') or '').strip()
        option = (q.get('옵션정보') or '').strip()
        key = (name, option)

        in_jp = key in jp_map
        in_kr = key in kr_map

        if in_jp and in_kr:
            conflict_keys.add(key)
            kr_orders.append(q)  # KR 우선 (배송준비 전환 후 KSE OMS 자동 수집)
        elif in_kr:
            kr_orders.append(q)
        elif in_jp:
            jp_orders.append(q)
        else:
            unknown_orders.append(q)

    return jp_orders, kr_orders, unknown_orders, conflict_keys


def _render_classify_result(jp, kr, unknown, conflicts):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 신규주문", len(jp) + len(kr) + len(unknown))
    c2.metric("🇰🇷 국내 출고", len(kr))
    c3.metric("🇯🇵 일본 출고", len(jp))
    c4.metric("🆕 미매핑", len(unknown))

    if conflicts:
        st.warning(
            f"⚠️ 양쪽 채널 모두 활성 매핑인 (상품명, 옵션) {len(conflicts)}건 — "
            "KR 우선으로 분류됨. 어드민 → 🔧 상품 매핑에서 한쪽만 활성으로 설정 권장."
        )
        with st.expander("충돌 키 목록", expanded=False):
            st.dataframe(pd.DataFrame([
                {'상품명': k[0], '옵션': k[1] or '(없음)'} for k in sorted(conflicts)
            ]), hide_index=True, width="stretch")

    if unknown:
        st.error(
            f"🆕 미매핑 {len(unknown)}건 — 어드민 → 🔧 상품 매핑에서 등록 후 다시 가져오기. "
            "JP 출고일 경우 채널 = 'Qoo10 일본 출고' / KR 출고일 경우 채널 = 'Qoo10 국내 출고'."
        )
        seen = set()
        rows = []
        for q in unknown:
            k = ((q.get('상품명') or '').strip(), (q.get('옵션정보') or '').strip())
            if k in seen:
                continue
            seen.add(k)
            rows.append({
                '상품명': k[0],
                '옵션': k[1] or '(없음)',
                '대표 주문번호': q.get('주문번호', ''),
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_kr_action(kr_orders):
    """KR 분기 — SetSellerCheckYN_V2 호출 placeholder (Phase C-4 에서 실제 호출)."""
    if not kr_orders:
        return
    st.markdown("---")
    st.markdown("### 🇰🇷 국내 출고 분기 (한국 다원 → KSE → 일본)")
    st.caption(
        f"KR 활성 매핑 {len(kr_orders)} 건 — 배송준비(stat=3) 전이 후 KSE OMS 국내가 "
        f"자동 수집. 발송예정일은 KST 오늘 ({kst_today().strftime('%Y-%m-%d')})."
    )

    # 주문 미리보기
    df = pd.DataFrame([{
        '주문번호': q.get('주문번호', ''),
        '장바구니번호': q.get('장바구니번호', ''),
        '상품명': (q.get('상품명') or '')[:40],
        '옵션': (q.get('옵션정보') or '')[:30],
        '수량': q.get('수량', 1),
    } for q in kr_orders[:50]])
    st.dataframe(df, hide_index=True, width="stretch")
    if len(kr_orders) > 50:
        st.caption(f"… 50/{len(kr_orders)} 행 표시")

    if st.button(
        f"🚚 KR {len(kr_orders)}건 배송준비로 전환 (Phase C-4 구현 예정)",
        disabled=True, width="stretch", key="kr_send_ready_btn",
    ):
        st.info("아직 구현 전")


def _render_jp_action(jp_orders):
    """JP 분기 — 일본 출고 탭으로 안내."""
    if not jp_orders:
        return
    st.markdown("---")
    st.markdown("### 🇯🇵 일본 출고 분기 (KSE 일본 직접)")
    st.caption(
        f"JP 활성 매핑 {len(jp_orders)} 건 — 신규 상태 그대로. "
        "**일본 출고** 탭으로 이동해서 출고요청서 생성/송장 등록 진행."
    )
    df = pd.DataFrame([{
        '주문번호': q.get('주문번호', ''),
        '장바구니번호': q.get('장바구니번호', ''),
        '상품명': (q.get('상품명') or '')[:40],
        '옵션': (q.get('옵션정보') or '')[:30],
        '수량': q.get('수량', 1),
    } for q in jp_orders[:50]])
    st.dataframe(df, hide_index=True, width="stretch")
    if len(jp_orders) > 50:
        st.caption(f"… 50/{len(jp_orders)} 행 표시")


def render():
    st.markdown(
        "QSM API로 신규주문(stat=2) 가져오기 → 매핑 활성여부 lookup 으로 KR/JP 자동 분류. "
        "**KR 활성 매핑** = 한국 다원→KSE→일본 / **JP 활성 매핑** = 일본 KSE 직접 출고."
    )

    # QSM API 자격증명 확인
    api_available = qapi.has_credentials()
    if not api_available:
        st.error(
            "Qoo10 API 자격증명이 등록되지 않음. "
            "(우선 'Qoo10 일본 출고' 채널 사이드바에서 등록 — 통합 채널 사이드바 후속 작업)"
        )
        return

    today = kst_today()
    if st.button("🔄 QSM에서 가져오기 (최근 30일 신규주문)", type="primary",
                 width="stretch", key="cu_fetch_btn"):
        sd = (today - __import__('datetime').timedelta(days=30)).strftime('%Y%m%d')
        ed = today.strftime('%Y%m%d')
        with st.spinner("QSM API 조회 중..."):
            try:
                sak = qapi.get_sak()
                api_orders = qapi.fetch_orders(sak, sd, ed)
            except Exception as ex:
                st.error(f"API 호출 실패: {ex}")
                return
        qsm_rows = [qapi.api_response_to_qsm_dict(o) for o in api_orders]
        st.session_state['cu_qsm_rows'] = qsm_rows
        st.success(f"✅ {len(qsm_rows)}건 가져옴")
        st.rerun()

    qsm_rows = st.session_state.get('cu_qsm_rows', [])
    if not qsm_rows:
        st.info("아직 가져오지 않았습니다. 위 버튼 클릭.")
        return

    st.markdown("---")
    st.markdown(f"### 📊 분류 결과 (총 {len(qsm_rows)}건)")

    jp_map = _m.load_for_channel(CHANNEL_JP, active_only=True)
    kr_map = _m.load_for_channel(CHANNEL_KR, active_only=True)

    jp_orders, kr_orders, unknown_orders, conflicts = _classify(qsm_rows, jp_map, kr_map)
    _render_classify_result(jp_orders, kr_orders, unknown_orders, conflicts)

    _render_kr_action(kr_orders)
    _render_jp_action(jp_orders)
    # ↑ 위에서 KR(국내) 먼저 처리(배송준비 전환) → JP(일본) 출고 탭으로 진행 순서

    st.markdown("---")
    if st.button("🗑 가져온 주문 초기화", key="cu_reset_btn"):
        st.session_state.pop('cu_qsm_rows', None)
        st.rerun()
