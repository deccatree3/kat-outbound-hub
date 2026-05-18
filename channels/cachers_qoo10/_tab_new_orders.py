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


def _collect_via_csv(work_date=None, sequence=None):
    """QSM detail/brief CSV 2개 업로드 → cu_qsm_rows + qoo10_detail/brief bytes."""
    st.caption(
        "QSM > 배송관리 > 배송요청 > 신규주문에서 받은 detail / brief CSV 2개를 업로드. "
        "파일명에 `detail` / `brief` 가 포함되면 자동 분류."
    )

    uploaded = st.file_uploader(
        "QSM 자료 2개 업로드 (detail + brief)",
        type=['csv'], accept_multiple_files=True,
        key="cu_csv_upload",
    )
    if uploaded:
        for f in uploaded:
            nm = f.name.lower()
            content = f.getvalue()
            if 'detail' in nm:
                st.session_state['qoo10_detail_bytes'] = content
                st.session_state['qoo10_detail_name'] = f.name
            elif 'brief' in nm:
                st.session_state['qoo10_brief_bytes'] = content
                st.session_state['qoo10_brief_name'] = f.name
                st.session_state['qoo10_brief_work_date'] = work_date
                st.session_state['qoo10_brief_sequence'] = sequence
                # 미확정 — 하단 '주문수집 확정' 버튼 클릭 시 DB 저장
                st.session_state.pop('qoo10_brief_id', None)
                st.session_state.pop('qoo10_tab1_confirmed', None)
                st.session_state.pop('cu_kr_transitioned', None)
                st.session_state.pop('cu_kr_last_result', None)

    det_ok = bool(st.session_state.get('qoo10_detail_bytes'))
    brief_ok = bool(st.session_state.get('qoo10_brief_bytes'))
    det_check = '✅' if det_ok else ''
    brief_check = '✅' if brief_ok else ''
    st.markdown(
        "<div style='font-size:0.85em'>\n\n"
        "| 구분 | 파일명 예시 | 취합 |\n"
        "|------|------------|:---:|\n"
        f"| 배송요청 상세 | `DeliveryManagement_detail_*.csv` | {det_check} |\n"
        f"| 배송요청 요약 | `DeliveryManagement_brief_*.csv` | {brief_check} |\n\n"
        "</div>",
        unsafe_allow_html=True,
    )

    if det_ok and brief_ok:
        if st.button("📥 분류 진행", key="cu_csv_classify_btn",
                     type="primary", width="stretch"):
            try:
                qsm_rows = qgen.parse_qsm_csv(st.session_state['qoo10_detail_bytes'])
            except Exception as ex:
                st.error(f"detail CSV 파싱 실패: {ex}")
                return
            st.session_state['cu_qsm_rows'] = qsm_rows
            st.session_state['cu_collect_mode'] = 'csv'
            st.success(f"✅ {len(qsm_rows)}건 로드")
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
    st.markdown("자동 또는 수동 방법으로 QSM의 신규주문을 수집해주세요.")

    api_available = qapi.has_credentials()
    qsm_rows = st.session_state.get('cu_qsm_rows', [])

    if not qsm_rows:
        # 발주계획 picker (탭 1/2/3 통일 UI — 탭 1 만 '+ 신규 작업' 옵션 포함)
        from channels.cachers_qoo10._brief_picker import render_brief_picker
        result = render_brief_picker(
            key_prefix='cu_tab1', allow_new=True,
            title="발주계획 선택",
        )
        if result is None:
            st.info("위에서 '+ 신규 작업' 또는 기존 발주계획 선택.")
            return
        if not result.get('is_new'):
            # 기존 brief 선택 시: 이미 수집된 batch — 탭 2/3 에서 후속 작업.
            st.info(
                f"📋 발주계획 #{result['id']} 는 이미 수집됨. "
                "탭 2/3 에서 후속 작업 진행 (배송상태 변경/일본 출고 등)."
            )
            return

        # 신규 작업 모드
        work_date = result['work_date']
        sequence = result['sequence']

        # 수집 모드 선택
        mode_options = (["자동 (QSM API)", "수동 (CSV 2개 업로드)"]
                        if api_available else ["수동 (CSV 2개 업로드)"])
        mode = st.radio(
            "수집 방식",
            options=mode_options, horizontal=True, key="cu_collect_mode_radio",
            help=None if api_available else
                 "Qoo10 API 자격증명이 등록되지 않아 자동 수집 비활성화됨",
        )
        if mode.startswith("자동"):
            _collect_via_api(work_date=work_date, sequence=sequence)
        else:
            _collect_via_csv(work_date=work_date, sequence=sequence)
        return

    # 수집 완료 — 분류 결과
    mode_label = '자동(API)' if st.session_state.get('cu_collect_mode') == 'api' else '수동(CSV)'
    wd = st.session_state.get('qoo10_brief_work_date')
    sq = st.session_state.get('qoo10_brief_sequence')
    session_tag = (f" · {wd.strftime('%Y-%m-%d')} / {sq}차"
                   if wd and sq else "")
    st.caption(
        f"수집 방식: **{mode_label}**{session_tag} · 일본 출고 탭에서 재사용 가능"
    )

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
        # Qoo10 SetSellerCheckYN_V2 의 OrderNo = 장바구니번호(packNo).
        # 주문번호(orderNo) 전송 시 -10001 "OrderNo format error".
        order_nos = [str(q.get('장바구니번호', '')).strip() for q in kr_orders
                     if str(q.get('장바구니번호', '')).strip()]

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
                        f"❌ 호출 실패 (ResultCode={result['code']}, ResultMsg={result['msg']}). "
                        f"전송 OrderNo(packNo)={result.get('order_nos', '')}"
                    )

    # ─── 페이지 하단 — 주문수집 확정 + 수집 초기화 ─────
    # 확정 여부는 탭 1 전용 플래그(qoo10_tab1_confirmed) 로 추적. qoo10_brief_id 는
    # 탭 2/3 picker 와 공유되어 false-positive ('확정됨') 표시 원인이라 사용 X.
    st.markdown("---")
    tab1_confirmed = st.session_state.get('qoo10_tab1_confirmed')
    confirmed_bid = st.session_state.get('qoo10_brief_id') if tab1_confirmed else None
    if confirmed_bid:
        st.success(f"📋 주문수집 확정됨 — brief #{confirmed_bid} (2/3 탭 발주계획 드롭다운에 노출).")
    else:
        # KR 매핑 미처리 시 비활성. KR 없음 또는 배송상태 변경 완료 시 활성.
        confirm_disabled = bool(kr_orders) and not kr_done
        if st.button(
            "📋 주문수집 확정", type="primary", width="stretch", key="cu_confirm_collect",
            disabled=confirm_disabled,
            help=(
                "KR 매핑 주문의 배송상태 변경 완료 후 활성화됩니다."
                if confirm_disabled else
                "brief 를 DB 에 저장. 2/3 탭에서 이 batch 를 선택할 수 있게 됨."
            ),
        ):
            content = st.session_state.get('qoo10_brief_bytes')
            fname = st.session_state.get('qoo10_brief_name')
            wd_save = st.session_state.get('qoo10_brief_work_date')
            sq_save = st.session_state.get('qoo10_brief_sequence')
            if not content or not fname:
                st.error("brief 데이터 없음 — 재수집 필요.")
            else:
                try:
                    bid = qgen.save_pending_brief(
                        content, fname, len(qsm_rows),
                        work_date=wd_save, sequence=sq_save,
                    )
                    st.session_state['qoo10_brief_id'] = bid
                    st.session_state['qoo10_tab1_confirmed'] = True
                    _cache.invalidate_all()
                    st.success(f"✅ 주문수집 확정 — brief #{bid}")
                    st.rerun()
                except Exception as ex:
                    st.error(f"저장 실패: {ex}")

    if st.button("🗑 수집 초기화 (재수집)", key="cu_reset_btn"):
        _clear_collected_state()
        st.rerun()
