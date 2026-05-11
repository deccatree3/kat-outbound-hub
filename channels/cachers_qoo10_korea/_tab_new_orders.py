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
from channels import _db_cache as _cache


CHANNEL_JP = 'qoo10_japan'
CHANNEL_KR = 'cachers_qoo10_kr'


def _classify(qsm_rows, jp_map, kr_map):
    """QSM dict 행들 → JP/KR/미매핑/충돌 분류.

    매핑 lookup 은 활성(is_active=TRUE) 매핑만 사용.
    같은 (상품명, 옵션) 이 양쪽 채널 모두 활성이면 운영 오류 — 분류 보류 (conflict_orders).
    양쪽 모두 비활성이거나 매핑 없음 → 미매핑.
    """
    jp_orders = []
    kr_orders = []
    unknown_orders = []
    conflict_orders = []  # 양쪽 활성 = 운영 오류, 처리 보류

    for q in qsm_rows:
        name = (q.get('상품명') or '').strip()
        option = (q.get('옵션정보') or '').strip()
        key = (name, option)

        in_jp = key in jp_map
        in_kr = key in kr_map

        if in_jp and in_kr:
            conflict_orders.append(q)
        elif in_kr:
            kr_orders.append(q)
        elif in_jp:
            jp_orders.append(q)
        else:
            unknown_orders.append(q)

    return jp_orders, kr_orders, unknown_orders, conflict_orders


def _render_classify_result(jp, kr, unknown, conflicts):
    c1, c2, c3, c4, c5 = st.columns(5)
    total = len(jp) + len(kr) + len(unknown) + len(conflicts)
    c1.metric("총 신규주문", total)
    c2.metric("국내 출고", len(kr))
    c3.metric("일본 출고", len(jp))
    c4.metric("🆕 미매핑", len(unknown))
    c5.metric("⚠️ 충돌", len(conflicts),
              help="양쪽 채널 모두 활성 매핑 — 한쪽만 활성으로 토글 필요")

    if conflicts:
        # 키별 주문 카운트
        from collections import defaultdict
        by_key = defaultdict(list)
        for q in conflicts:
            k = ((q.get('상품명') or '').strip(), (q.get('옵션정보') or '').strip())
            by_key[k].append(q)
        st.error(
            f"⚠️ **양쪽 채널 모두 활성 매핑** — 주문 {len(conflicts)}건 / 충돌 키 {len(by_key)}개. "
            "운영 오류. 어드민 → 🔧 상품 매핑에서 한쪽만 활성으로 토글 후 재가져오기. "
            "이 행들은 분류되지 않음 (KR/JP 어디로도 보내지 않음)."
        )
        rows = []
        for k, qs in by_key.items():
            rows.append({
                '상품명': k[0],
                '옵션': k[1] or '(없음)',
                '영향 주문수': len(qs),
                '대표 주문번호': qs[0].get('주문번호', ''),
            })
        with st.expander(
            f"⚠️ 충돌 키 목록 ({len(rows)}개 키 / 주문 {len(conflicts)}건)", expanded=True
        ):
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    if unknown:
        from collections import defaultdict
        by_key = defaultdict(list)
        for q in unknown:
            k = ((q.get('상품명') or '').strip(), (q.get('옵션정보') or '').strip())
            by_key[k].append(q)
        st.error(
            f"🆕 미매핑 — 주문 {len(unknown)}건 / 키 {len(by_key)}개. "
            "어드민 → 🔧 상품 매핑에서 등록 후 다시 가져오기. "
            "JP 출고일 경우 채널 = 'Qoo10 일본 출고' / KR 출고일 경우 채널 = 'Qoo10 국내 출고'."
        )
        rows = []
        for k, qs in by_key.items():
            rows.append({
                '상품명': k[0],
                '옵션': k[1] or '(없음)',
                '영향 주문수': len(qs),
                '대표 주문번호': qs[0].get('주문번호', ''),
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


DEST_LABEL = {
    'jp': '일본',
    'kr': '국내',
    'unknown': '미매핑',
    'conflict': '충돌',
}


def _render_product_summary(jp_orders, kr_orders, unknown_orders, conflicts):
    """수집된 주문을 (상품명, 옵션) 별로 묶고 출고처 라벨과 함께 표시."""
    from collections import defaultdict

    def _qty(q) -> int:
        try:
            return int(q.get('수량') or 1)
        except Exception:
            return 1

    bucket = defaultdict(lambda: {'qty': 0, 'dest': None})
    for tag, orders in (('jp', jp_orders), ('kr', kr_orders),
                        ('unknown', unknown_orders), ('conflict', conflicts)):
        for q in orders:
            key = ((q.get('상품명') or '').strip(),
                   (q.get('옵션정보') or '').strip())
            bucket[key]['qty'] += _qty(q)
            bucket[key]['dest'] = tag  # 분류는 상호배타이므로 마지막 값으로 유지

    if not bucket:
        return

    st.markdown("---")
    st.markdown("### 📦 상품별 출고처")
    st.caption("같은 (상품명, 옵션) 기준으로 합산. 출고처 = 활성 매핑이 있는 채널.")

    rows = []
    for (name, option), v in bucket.items():
        rows.append({
            '상품명': name,
            '옵션': option or '(없음)',
            '수량': v['qty'],
            '출고': DEST_LABEL.get(v['dest'], v['dest'] or ''),
        })
    rows.sort(key=lambda r: (r['출고'], r['상품명']))
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")




def _collect_via_api(work_date=None, sequence=None):
    """QSM API → cu_qsm_rows + qoo10_detail/brief bytes (일본 출고 탭에서 재사용)."""
    import datetime as _dt
    api_status = qapi.get_credentials_status()
    if api_status.get('expires_at') and api_status.get('days_remaining') is not None:
        icon = {'green': '🟢', 'yellow': '🟡', 'red': '🔴', 'expired': '⚫'}.get(
            api_status['level'], '🔑')
        d = api_status['days_remaining']
        exp_msg = (f"{icon} API 키 만료일: **{api_status['expires_at']}** "
                   f"({'D-' + str(d) if d >= 0 else f'{abs(d)}일 경과'})")
        if api_status['level'] in ('expired', 'red'):
            st.error(exp_msg + " — 사이드바에서 갱신 필요")
        elif api_status['level'] == 'yellow':
            st.warning(exp_msg)
        else:
            st.caption(exp_msg)

    today = kst_today()
    if st.button("🔄 QSM에서 가져오기 (최근 30일 신규주문)", type="primary",
                 width="stretch", key="cu_fetch_btn"):
        sd = (today - _dt.timedelta(days=30)).strftime('%Y%m%d')
        ed = today.strftime('%Y%m%d')
        with st.spinner("QSM API 조회 중..."):
            try:
                sak = qapi.get_sak()
                api_orders = qapi.fetch_orders(sak, sd, ed, qapi.SHIPPING_STAT_REQUEST)
            except Exception as ex:
                st.error(f"API 호출 실패: {ex}")
                return
        if not api_orders:
            st.warning("📭 해당 기간에 신규주문이 없습니다.")
            return
        qsm_rows = [qapi.api_response_to_qsm_dict(o) for o in api_orders]
        # 일본 출고 탭에서 step2~ 사용할 detail/brief bytes
        detail_bytes = qapi.build_detail_csv_bytes(api_orders)
        brief_bytes = qapi.build_brief_csv_bytes(api_orders)
        ts = _dt.datetime.now().strftime('%Y%m%d_%H%M')
        st.session_state['cu_qsm_rows'] = qsm_rows
        st.session_state['cu_collect_mode'] = 'api'
        st.session_state['qoo10_detail_bytes'] = detail_bytes
        st.session_state['qoo10_detail_name'] = f"API_DeliveryManagement_detail_{ts}.csv"
        st.session_state['qoo10_brief_bytes'] = brief_bytes
        st.session_state['qoo10_brief_name'] = f"API_DeliveryManagement_brief_{ts}.csv"
        st.session_state['qoo10_brief_work_date'] = work_date
        st.session_state['qoo10_brief_sequence'] = sequence
        # 미확정 — 하단 '주문수집 확정' 버튼 클릭 시 DB 저장
        st.session_state.pop('qoo10_brief_id', None)
        st.session_state.pop('qoo10_tab1_confirmed', None)
        st.session_state.pop('cu_kr_transitioned', None)
        st.session_state.pop('cu_kr_last_result', None)
        st.success(f"✅ {len(qsm_rows)}건 가져옴 — 하단 '주문수집 확정' 버튼으로 저장")
        st.rerun()


def _clear_collected_state():
    for k in ('cu_qsm_rows', 'cu_collect_mode', 'cu_kr_last_result',
              'cu_kr_transitioned',
              'qoo10_detail_bytes', 'qoo10_detail_name',
              'qoo10_brief_bytes', 'qoo10_brief_name', 'qoo10_brief_id',
              'qoo10_brief_work_date', 'qoo10_brief_sequence',
              'qoo10_tab1_confirmed'):
        st.session_state.pop(k, None)


def render():
    st.markdown("QSM API 로 신규주문 수집 → 국내 출고 배송상태 변경.")

    api_available = qapi.has_credentials()
    qsm_rows = st.session_state.get('cu_qsm_rows', [])

    if not qsm_rows:
        # API 자동 수집만 — 발주계획 picker / 주문수집 확정 제거.
        if not api_available:
            st.error(
                "❌ Qoo10 API 자격증명이 없습니다. "
                "사이드바에서 자격증명 등록 후 다시 시도하세요."
            )
            return
        _collect_via_api()
        return

    # 수집 완료 — 분류 결과
    st.caption(f"수집 방식: **자동(API)** · {len(qsm_rows)}건")

    st.markdown("---")
    st.markdown(f"### 📊 분류 결과 (총 {len(qsm_rows)}건)")

    jp_map = _cache.load_mapping(CHANNEL_JP, active_only=True)
    kr_map = _cache.load_mapping(CHANNEL_KR, active_only=True)

    jp_orders, kr_orders, unknown_orders, both_active = _classify(qsm_rows, jp_map, kr_map)
    _render_classify_result(jp_orders, kr_orders, unknown_orders, both_active)
    _render_product_summary(jp_orders, kr_orders, unknown_orders, both_active)
    # 국내 출고 분기 상세 표는 탭 2 에 있음. 여기는 액션 버튼만 노출.

    # ─── 국내 출고 배송상태 변경 (주문수집 확정 위) ─────
    # 성공 시 cu_qsm_rows 는 그대로 두고 cu_kr_transitioned 플래그만 set.
    # brief 는 수집 시점의 전체(4건) 그대로 저장 → 탭 2/3 가 분류 결과 표시.
    kr_done = bool(st.session_state.get('cu_kr_transitioned'))
    if kr_orders:
        today = kst_today()
        today_str = today.strftime('%Y-%m-%d')
        today_yyyymmdd = today.strftime('%Y%m%d')
        order_nos = [str(q.get('주문번호', '')).strip() for q in kr_orders
                     if str(q.get('주문번호', '')).strip()]

        st.markdown("---")
        if kr_done:
            last_result = st.session_state.get('cu_kr_last_result') or {}
            st.success(
                f"✅ 배송상태 변경 완료 — {last_result.get('count', len(order_nos))}건. "
                f"({last_result.get('msg', 'SUCCESS')})"
            )
        else:
            last_result = st.session_state.get('cu_kr_last_result')
            if last_result and not last_result['ok']:
                st.error(
                    f"❌ 직전 호출 실패 (ResultCode={last_result['code']}, "
                    f"ResultMsg={last_result['msg']})"
                )
            btn = f"🚚 국내 출고 {len(order_nos)}건 배송상태 변경 (발송예정일 {today_str})"
            if st.button(btn, type="primary", width="stretch",
                         key="cu_kr_send_ready_btn_tab1"):
                try:
                    sak = qapi.get_sak()
                except Exception as ex:
                    st.error(f"SAK 발급 실패: {ex}")
                    return
                with st.spinner(f"SetSellerCheckYN_V2 호출 중 ({len(order_nos)}건)..."):
                    try:
                        result = qapi.set_seller_check_yn(sak, order_nos, today_yyyymmdd)
                    except Exception as ex:
                        st.error(f"API 호출 실패: {ex}")
                        return
                st.session_state['cu_kr_last_result'] = result
                if result['ok']:
                    # cu_qsm_rows 는 그대로 — brief 는 수집 시점 전체 유지.
                    st.session_state['cu_kr_transitioned'] = True
                    st.success(
                        f"✅ {len(order_nos)}건 배송상태 변경 완료. "
                        "이후 KSE OMS 국내가 자동 수집."
                    )
                    st.rerun()
                else:
                    st.error(
                        f"❌ 호출 실패 (ResultCode={result['code']}, ResultMsg={result['msg']})."
                    )

    # ─── 페이지 하단 — 수집 초기화 ─────
    st.markdown("---")
    if st.button("🗑 수집 초기화 (재수집)", key="cu_reset_btn"):
        _clear_collected_state()
        st.rerun()
