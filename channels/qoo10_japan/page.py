"""
Qoo10 일본 출고 Streamlit 페이지.

자매 프로젝트(`kat-kse-3pl-japan/dashboard.py` line 332-410, 576-1576)에서 이식.
3 탭 구조:
  - 출고요청 (6단계 stepper)
  - 출고 이력
  - 상품 매핑 관리

사이드바에는 자격증명 expander를 페이지 진입 시에만 추가한다.
"""
import datetime

import pandas as pd
import streamlit as st

from db import pg
from qoo10 import api_client as qapi
from qoo10 import generator as qgen


def render_credentials_sidebar():
    """Qoo10 API 자격증명 사이드바 expander.
    페이지 진입 시 1회 호출. 다른 채널과 충돌 없도록 key prefix 'qoo10_sb_'.
    """
    with st.sidebar.expander("🔐 Qoo10 API 자격증명", expanded=False):
        status = qapi.get_credentials_status()
        if status['configured']:
            exp = status['expires_at']
            days = status['days_remaining']
            level = status['level']
            if exp and days is not None:
                icon = {'green': '🟢', 'yellow': '🟡', 'red': '🔴', 'expired': '⚫'}.get(level, '🔑')
                msg = (f"{icon} 만료일 **{exp}** "
                       f"({'D-' + str(days) if days >= 0 else f'{abs(days)}일 경과'})")
                if level == 'expired' or level == 'red':
                    st.error(msg)
                elif level == 'yellow':
                    st.warning(msg)
                else:
                    st.success(msg)
            else:
                st.success("✅ 자격증명 등록됨")
            if status.get('updated_at'):
                st.caption(f"마지막 갱신: `{status['updated_at']}`")
        else:
            st.warning("⚠️ 자격증명 미등록")

        st.caption("입력란을 비워두면 기존 값이 유지됩니다 (부분 갱신 가능)")

        api_key_in = st.text_input(
            "Certification Key", type="password",
            placeholder="GiosisCertificationKey",
            key="qoo10_sb_api_key",
        )
        user_id_in = st.text_input(
            "API ID (또는 판매자 ID)", placeholder="adminkatchers 등",
            key="qoo10_sb_user_id",
        )
        password_in = st.text_input(
            "비밀번호", type="password", key="qoo10_sb_password",
        )
        expires_in = st.date_input(
            "만료일",
            value=status['expires_at'] if status.get('expires_at')
                  else datetime.date.today() + datetime.timedelta(days=365),
            key="qoo10_sb_expires_at",
            help="Qoo10에서 통보받은 키 만료일을 입력 (보통 발급 후 1년)",
        )

        b1, b2 = st.columns(2)
        with b1:
            if st.button("💾 저장", key="qoo10_sb_save", width="stretch", type="primary"):
                ok = qapi.save_credentials_to_db(
                    api_key=api_key_in.strip() or None,
                    user_id=user_id_in.strip() or None,
                    password=password_in.strip() or None,
                    expires_at=expires_in,
                )
                if ok:
                    st.success("저장 완료")
                    st.rerun()
                else:
                    st.error("저장 실패 (DB 연결 확인)")
        with b2:
            if st.button("🧪 연결 테스트", key="qoo10_sb_test", width="stretch",
                         help="저장 전이라도 입력란의 값으로 즉시 시도"):
                loaded = qapi.load_credentials()
                t_api = api_key_in.strip() or loaded.get('api_key')
                t_uid = user_id_in.strip() or loaded.get('user_id')
                t_pw = password_in.strip() or loaded.get('password')
                if not all([t_api, t_uid, t_pw]):
                    st.error("3개 값(Certification Key / API ID / 비밀번호) 모두 필요합니다")
                else:
                    try:
                        sak = qapi.get_sak(api_key=t_api, user_id=t_uid, password=t_pw)
                        st.success(f"✅ 인증 성공 (SAK len={len(sak)})")
                    except Exception as ex:
                        st.error(f"❌ {ex}")


def _render_stepper(active: int):
    """6단계 진행 표시 (st.button 기반, 클릭 시 단계 전환). active = 현재 단계(1-6)."""
    steps = [
        (1, "1. QSM 주문 취합", "QSM 파일 2개 업로드"),
        (2, "2. KSE 출고요청서 생성", "OMS 업로드 파일 다운로드"),
        (3, "3. KSE 출고요청서 등록", "KSE OMS 업로드 안내"),
        (4, "4. KSE 송장번호 취합", "KSE OMS 주문 내역 업로드"),
        (5, "5. QSM 송장 파일 생성", "송장 brief 파일 다운로드"),
        (6, "6. QSM 송장 등록", "QSM 업로드 안내"),
    ]
    cols = st.columns([4, 0.4, 4, 0.4, 4, 0.4, 4, 0.4, 4, 0.4, 4])
    for ai in (1, 3, 5, 7, 9):
        cols[ai].markdown(
            "<div style='text-align:center;color:#BDBDBD;font-size:1.4em;padding-top:0.4em'>→</div>",
            unsafe_allow_html=True,
        )
    for ci, (n, title, desc) in zip((0, 2, 4, 6, 8, 10), steps):
        with cols[ci]:
            btype = "primary" if n == active else "secondary"
            if st.button(title, key=f"qoo10_step_btn_{n}", type=btype, width="stretch"):
                st.session_state['qoo10_step'] = n
                st.rerun()
            st.caption(desc)


def _step1_qsm_collect():
    st.markdown("#### ① QSM 주문 취합")

    api_available = qapi.has_credentials()
    mode_options = (["QSM API로 가져오기 (자동)", "CSV 업로드 (수동)"]
                    if api_available else ["CSV 업로드 (수동)"])
    mode = st.radio(
        "취합 방식",
        options=mode_options,
        horizontal=True,
        key="step1_mode",
        help=None if api_available else
             "Qoo10 API 자격증명이 등록되지 않아 자동 취합 비활성화됨",
    )

    if mode == "QSM API로 가져오기 (자동)":
        api_status = qapi.get_credentials_status()
        if api_status['expires_at'] and api_status['days_remaining'] is not None:
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
        st.caption("QSM **신규주문**(배송요청 상태) = QSM 배송관리 화면의 '신규주문 N건'과 동일.")
        today = datetime.date.today()

        if st.button("🔄 QSM에서 가져오기", key="step1_api_fetch",
                     type="primary", width="stretch"):
            sd = (today - datetime.timedelta(days=30)).strftime('%Y%m%d')
            ed = today.strftime('%Y%m%d')
            with st.spinner("QSM API 조회 중 (최근 30일 신규주문)..."):
                try:
                    sak = qapi.get_sak()
                    raw = qapi.fetch_orders(sak, sd, ed, qapi.SHIPPING_STAT_REQUEST)
                except Exception as ex:
                    st.error(f"API 호출 실패: {ex}")
                    raw = None

            if raw is not None:
                if not raw:
                    st.warning("📭 해당 기간에 배송요청 상태 주문이 없습니다.")
                else:
                    detail_bytes = qapi.build_detail_csv_bytes(raw)
                    brief_bytes = qapi.build_brief_csv_bytes(raw)
                    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M')
                    detail_name = f"API_DeliveryManagement_detail_{ts}.csv"
                    brief_name = f"API_DeliveryManagement_brief_{ts}.csv"
                    st.session_state['qoo10_detail_bytes'] = detail_bytes
                    st.session_state['qoo10_detail_name'] = detail_name
                    st.session_state['qoo10_brief_bytes'] = brief_bytes
                    st.session_state['qoo10_brief_name'] = brief_name
                    try:
                        bid = qgen.save_pending_brief(brief_bytes, brief_name, len(raw))
                        st.session_state['qoo10_brief_id'] = bid
                    except Exception as ex:
                        st.warning(f"brief 임시저장 실패 (세션 내에서는 사용 가능): {ex}")
                    st.success(f"✅ {len(raw)}건 가져옴")
                    st.rerun()

        det_loaded = bool(st.session_state.get('qoo10_detail_bytes'))
        brief_loaded = bool(st.session_state.get('qoo10_brief_bytes'))
        if det_loaded and brief_loaded:
            cnt = len(qgen.parse_qsm_csv(st.session_state['qoo10_brief_bytes']))
            st.info(f"📥 취합 완료: **{cnt}건** "
                    f"(`{st.session_state.get('qoo10_brief_name')}`)")
            if st.button("🗑 가져온 데이터 비우기", key="step1_api_clear"):
                for k in ('qoo10_detail_bytes', 'qoo10_detail_name',
                          'qoo10_brief_bytes', 'qoo10_brief_name',
                          'qoo10_brief_id'):
                    st.session_state.pop(k, None)
                st.rerun()
            if st.button("다음 단계 →", key="goto_step2_api", type="primary"):
                st.session_state['qoo10_step'] = 2
                st.rerun()

    else:
        st.caption("QSM에서 다운로드한 detail / brief 파일 2개를 업로드하세요.")
        table_slot = st.empty()

        uploaded_q = st.file_uploader(
            "QSM 자료 2개를 업로드해주세요",
            type=['csv'], accept_multiple_files=True,
            key="qoo10_gen_files",
            help="파일명에 'detail' 포함 → 상세, 'brief' 포함 → 요약으로 자동 분류",
        )
        if uploaded_q:
            for f in uploaded_q:
                nm = f.name.lower()
                if 'detail' in nm:
                    st.session_state['qoo10_detail_bytes'] = f.getvalue()
                    st.session_state['qoo10_detail_name'] = f.name
                elif 'brief' in nm:
                    content = f.getvalue()
                    st.session_state['qoo10_brief_bytes'] = content
                    st.session_state['qoo10_brief_name'] = f.name
                    try:
                        brief_rows_cnt = len(qgen.parse_qsm_csv(content))
                        bid = qgen.save_pending_brief(content, f.name, brief_rows_cnt)
                        st.session_state['qoo10_brief_id'] = bid
                    except Exception as ex:
                        st.warning(f"brief 임시저장 실패 (세션 내에서는 사용 가능): {ex}")

        clear_c1, _ = st.columns([1, 4])
        with clear_c1:
            if st.button("🗑 모두 초기화", help="업로드 파일/진행 상태 초기화"):
                for k in ('qoo10_detail_bytes', 'qoo10_detail_name',
                          'qoo10_brief_bytes', 'qoo10_brief_name'):
                    st.session_state.pop(k, None)
                st.rerun()

        det_uploaded = bool(st.session_state.get('qoo10_detail_bytes'))
        brief_uploaded = bool(st.session_state.get('qoo10_brief_bytes'))

        det_check = '✅' if det_uploaded else ''
        brief_check = '✅' if brief_uploaded else ''
        table_slot.markdown(
            "<div style='font-size:0.75em'>\n\n"
            "| 구분 | 취합 경로 | 파일명 예시 | 취합 |\n"
            "|------|----------|------------|:-------:|\n"
            f"| 배송요청 상세 파일 | QSM > 배송/취소/미수취 > 배송관리 > 배송요청(상세보기) > 신규주문 숫자 클릭 > 전체주문 엑셀다운 | `DeliveryManagement_detail_YYYYMMDD_HHMM.csv` | {det_check} |\n"
            f"| 배송요청 요약 파일 | QSM > 배송/취소/미수취 > 배송관리 > 배송요청(요약보기) > 신규주문 숫자 클릭 > 전체주문 엑셀다운 | `DeliveryManagement_brief_YYYYMMDD_HHMM.csv` | {brief_check} |\n\n"
            "</div>",
            unsafe_allow_html=True,
        )

        if det_uploaded and brief_uploaded:
            st.success("✅ 두 파일 모두 업로드 완료. 다음 단계로 진행하세요.")
            if st.button("다음 단계 →", key="goto_step2", type="primary"):
                st.session_state['qoo10_step'] = 2
                st.rerun()


def _step2_outbound_generate():
    st.markdown("#### ② KSE 출고요청서 생성")
    st.caption("검수 지표를 확인 후 OMS 업로드용 xlsx를 다운로드하세요.")

    det_bytes = st.session_state.get('qoo10_detail_bytes')
    det_name = st.session_state.get('qoo10_detail_name')
    brief_uploaded = bool(st.session_state.get('qoo10_brief_bytes'))

    if not det_bytes or not brief_uploaded:
        st.error("⚠️ ① 단계에서 detail / brief 파일을 먼저 업로드하세요.")
        if st.button("← ① 단계로 이동"):
            st.session_state['qoo10_step'] = 1
            st.rerun()
        return

    try:
        rows = qgen.parse_qsm_csv(det_bytes)
        mappings = qgen.load_mappings()
        out_rows, errors, addr_changes = qgen.generate_outbound_rows(rows, mappings)
        audit = qgen.compute_audit(rows, out_rows, mappings)

        missing_errors = [e for e in errors if e['원인'] == '상품 매핑 없음']
        disabled_errors = [e for e in errors if e['원인'] == '매핑 비활성(취급 안함)']

        bid_now = st.session_state.get('qoo10_brief_id')
        brief_bytes_now = st.session_state.get('qoo10_brief_bytes')
        brief_name_now = st.session_state.get('qoo10_brief_name')
        if bid_now and brief_bytes_now:
            try:
                brief_cnt = len(qgen.parse_qsm_csv(brief_bytes_now))
                qgen.save_pending_brief(brief_bytes_now, brief_name_now,
                                        brief_cnt, len(disabled_errors))
            except Exception:
                pass

        japan_order_count = len(rows) - len(disabled_errors)
        audit_table = pd.DataFrame([
            {'구분': '총 주문 개수',                 '수량': len(rows)},
            {'구분': '국내 창고 출고 주문 수',       '수량': len(disabled_errors)},
            {'구분': '일본 창고 출고 주문 수',       '수량': japan_order_count},
            {'구분': 'KSE OMS 업로드 ROW 개수',      '수량': audit['upload_row_count']},
            {'구분': '일본 창고 출고 발송수',         '수량': audit['unique_carts']},
        ])
        st.dataframe(
            audit_table, hide_index=True, width="stretch",
            column_config={
                '구분': st.column_config.TextColumn(width="medium"),
                '수량': st.column_config.NumberColumn(width="small", format="%d"),
            },
        )

        st.caption(
            f"🚚 실제 출고 PCS (予定数量 합계): **{audit['total_picking_pcs']}** · "
            f"미매핑 **{len(missing_errors)}건** · 주소 정제 **{len(addr_changes)}건**"
        )

        if disabled_errors:
            with st.expander(f"📋 KSE 미취급 내역 ({len(disabled_errors)}건)", expanded=False):
                st.dataframe(
                    pd.DataFrame([
                        {
                            '장바구니번호': e.get('장바구니번호', ''),
                            '주문번호': e.get('주문번호', ''),
                            '상품명': e.get('상품명', ''),
                            '옵션정보': e.get('옵션정보', ''),
                        }
                        for e in disabled_errors
                    ]),
                    hide_index=True, width="stretch",
                )

        if missing_errors:
            uniq_missing_keys = {(e['상품명'], e['옵션정보']) for e in missing_errors}
            st.error(
                f"🆕 **신규 상품 매핑 필요**: 주문 {len(missing_errors)}건 "
                f"(고유 상품/옵션 조합 {len(uniq_missing_keys)}개). "
                "아래에서 등록하면 자동으로 페이지가 갱신되며 **파일은 유지**됩니다."
            )

            seen = set()
            uniq_missing = []
            for e in missing_errors:
                k = (e['상품명'], e['옵션정보'])
                if k not in seen:
                    seen.add(k)
                    uniq_missing.append(e)

            sku_catalog = qgen.load_kse_sku_catalog()
            if not sku_catalog:
                st.warning(
                    "KSE SKU 카탈로그가 비어있습니다 (이력 없음). "
                    "**상품 매핑** 탭에서 직접 등록하거나, 첫 출고 후 다시 시도하세요."
                )
            else:
                sku_options = [f"{s['sku_name']} ({s['sku_code']})" for s in sku_catalog]
                sku_by_label = {lbl: s for lbl, s in zip(sku_options, sku_catalog)}

                for idx, e in enumerate(uniq_missing):
                    with st.expander(
                        f"➕ 매핑 등록 [{idx+1}/{len(uniq_missing)}] : "
                        f"{e['상품명'][:50]}..." + (f" / {e['옵션정보'][:40]}" if e['옵션정보'] else ""),
                        expanded=(idx == 0),
                    ):
                        st.caption(f"**Qoo10 상품명**: `{e['상품명']}`")
                        st.caption(f"**Qoo10 옵션정보**: `{e['옵션정보'] or '(없음)'}`")
                        st.markdown("**KSE SKU 구성** (세트 상품이면 여러 행 추가)")

                        default_df = pd.DataFrame({
                            'KSE 상품': [sku_options[0]],
                            '수량': [1],
                        })
                        editor_key = f"mapeditor_{idx}_{hash((e['상품명'], e['옵션정보']))}"
                        edited = st.data_editor(
                            default_df,
                            column_config={
                                'KSE 상품': st.column_config.SelectboxColumn(
                                    options=sku_options, required=True, width="large",
                                    help="기존 출고 이력의 SKU에서 선택"),
                                '수량': st.column_config.NumberColumn(
                                    min_value=1, step=1, default=1, required=True, width="small"),
                            },
                            num_rows="dynamic",
                            key=editor_key,
                            hide_index=True,
                        )

                        if st.button("💾 매핑 저장", key=f"save_{editor_key}", type="primary"):
                            valid_rows = edited.dropna(subset=['KSE 상품'])
                            if valid_rows.empty:
                                st.error("최소 1개 SKU를 선택하세요.")
                            else:
                                skus_payload = []
                                for _, row in valid_rows.iterrows():
                                    sku_info = sku_by_label[row['KSE 상품']]
                                    qty = int(row['수량'] or 1)
                                    skus_payload.append(
                                        (sku_info['sku_code'], sku_info['sku_name'], qty)
                                    )
                                try:
                                    qgen.add_mapping(e['상품명'], e['옵션정보'], skus_payload)
                                    st.success(
                                        "매핑 저장 완료: "
                                        + " + ".join([f"{n}×{q}" for _, n, q in skus_payload])
                                    )
                                    st.rerun()
                                except Exception as ex:
                                    st.error(f"저장 실패: {ex}")

        addr_approved = True
        final_addr_map = {}
        if addr_changes:
            st.markdown("---")
            st.markdown("##### ⚠️ 주소 정제 검토 (사람의 최종 판단 필요)")
            st.caption(
                "자동 특수문자 제거 로직이 완벽하지 않아 **원본 주소와 정제 주소를 함께 표시**합니다. "
                "각 건마다 주소를 직접 확인하고, 필요시 **최종주소 컬럼을 수정**한 뒤 **승인** 체크를 켜세요. "
                "모두 승인되어야 출고요청서를 다운로드할 수 있습니다."
            )

            base = pd.DataFrame(addr_changes).copy()
            base['최종주소'] = base['정제주소']
            base['승인'] = False

            edited = st.data_editor(
                base,
                column_config={
                    '장바구니번호': st.column_config.TextColumn(disabled=True, width="small"),
                    '주문번호': st.column_config.TextColumn(disabled=True, width="small"),
                    '원본주소': st.column_config.TextColumn(disabled=True, width="medium"),
                    '정제주소': st.column_config.TextColumn(disabled=True, width="medium"),
                    '사유': st.column_config.TextColumn(disabled=True, width="medium",
                        help="원본에서 제거/치환된 문자와 이유"),
                    '최종주소': st.column_config.TextColumn(required=True, width="medium",
                        help="부적합하면 이 컬럼을 편집. 기본값=정제주소."),
                    '승인': st.column_config.CheckboxColumn(required=True),
                },
                hide_index=True, width="stretch",
                column_order=('장바구니번호', '주문번호', '원본주소', '정제주소',
                              '사유', '최종주소', '승인'),
                key="addr_review",
            )

            approved_count = int(edited['승인'].sum())
            total_to_approve = len(edited)
            addr_approved = (approved_count == total_to_approve)

            if addr_approved:
                st.success(f"주소 검토 완료 ({total_to_approve}건 모두 승인됨)")
            else:
                st.warning(f"승인 대기: {total_to_approve - approved_count}건 남음 (전체 {total_to_approve}건)")

            for _, r in edited.iterrows():
                if r['승인']:
                    final_addr_map[str(r['장바구니번호'])] = str(r['최종주소']).strip()

        st.markdown("---")

        if final_addr_map:
            for row in out_rows:
                cart = str(row.get('注文番号', ''))
                if cart in final_addr_map:
                    row['基本住所'] = final_addr_map[cart]
                    row['注文先基本住所'] = final_addr_map[cart]

        mapping_complete = not [e for e in errors if e['원인'] == '상품 매핑 없음']

        if japan_order_count == 0:
            st.info("📭 **일본 창고 출고 주문이 없습니다.** "
                    "모든 주문이 국내 창고 출고 대상이라 KSE 출고요청서를 만들 필요가 없습니다.")
            if st.button("🏁 작업 종료", key="finish_no_japan", type="primary", width="stretch"):
                try:
                    bid_close = st.session_state.get('qoo10_brief_id')
                    if bid_close:
                        qgen.mark_brief_consumed(bid_close)
                    for k in ('qoo10_detail_bytes', 'qoo10_detail_name',
                              'qoo10_brief_bytes', 'qoo10_brief_name',
                              'qoo10_brief_id', 'oms_bytes', 'oms_name'):
                        st.session_state.pop(k, None)
                    st.session_state['qoo10_step'] = 1
                    st.success("작업 종료 처리됨")
                    st.rerun()
                except Exception as ex:
                    st.error(f"작업 종료 실패: {ex}")
        elif out_rows:
            df_out = pd.DataFrame(out_rows)
            st.markdown("**미리보기**")
            st.dataframe(
                df_out[['倉庫コード', '商品コード', '予定数量', '注文番号',
                        '仕入先名/受取人名', '郵便番号コード', '基本住所']],
                width="stretch", hide_index=True,
            )

            if not mapping_complete:
                st.error(
                    "⚠️ **신규 상품 매핑이 남아 있어 다운로드할 수 없습니다.** "
                    "위의 '신규 상품 매핑 필요' 섹션에서 모두 등록하세요."
                )
            elif not addr_approved:
                st.error(
                    "⚠️ **주소 검토가 완료되지 않았습니다.** "
                    "주소 검토 표에서 모든 건을 승인해야 다운로드할 수 있습니다."
                )
            else:
                xlsx_bytes = qgen.build_outbound_xlsx(out_rows)
                today_str = datetime.date.today().strftime('%Y%m%d')
                try:
                    n_saved = qgen.save_outbound_log(
                        rows, out_rows, mappings, det_name or 'unknown.csv'
                    )
                    st.caption(f"🗂 출고 이력 DB 기록: {n_saved}건")
                except Exception as ex:
                    st.warning(f"DB 기록 실패 (다운로드는 가능): {ex}")
                st.download_button(
                    f"📥 Outbound_ship_conf_btoc_{today_str}.xlsx 다운로드",
                    data=xlsx_bytes,
                    file_name=f"Outbound_ship_conf_btoc_{today_str}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch",
                    type="primary",
                )
                st.info(
                    "📤 **출고요청서 다운로드 후 KSE OMS에 업로드 해주세요.**  \n"
                    "업로드 경로: **KSE OMS > 주문관리 > 주문업로드**"
                )
                if st.button("다음 단계 →", key="goto_step3", type="primary"):
                    st.session_state['qoo10_step'] = 3
                    st.rerun()
    except Exception as e:
        st.error(f"처리 중 오류: {e}")


def _step3_oms_upload_guide():
    st.markdown("#### ③ KSE 출고요청서 등록")
    st.caption("앞 단계에서 다운로드한 출고요청서를 KSE OMS에 업로드하는 방법 안내.")

    st.info(
        "📌 **KSE OMS 업로드 경로**  \n"
        "**KSE OMS > 주문관리 > 주문업로드**"
    )
    st.markdown("> _상세 안내(스크린샷)는 추후 추가 예정._")

    if st.button("다음 단계 →", key="goto_step4", type="primary"):
        st.session_state['qoo10_step'] = 4
        st.rerun()


def _step4_collect_waybills():
    st.markdown("#### ④ KSE 송장번호 취합")
    st.caption("작업 내역을 선택한 뒤 KSE OMS 주문(출고&입고) 내역 파일을 업로드하세요.")

    brief_bytes_t2 = st.session_state.get('qoo10_brief_bytes')
    brief_name_t2 = st.session_state.get('qoo10_brief_name')
    brief_id_t2 = st.session_state.get('qoo10_brief_id')

    if brief_bytes_t2 and not brief_id_t2:
        try:
            cnt = len(qgen.parse_qsm_csv(brief_bytes_t2))
            brief_id_t2 = qgen.save_pending_brief(brief_bytes_t2, brief_name_t2, cnt)
            st.session_state['qoo10_brief_id'] = brief_id_t2
        except Exception:
            pass

    pending_briefs = []
    try:
        pending_briefs = qgen.list_pending_briefs(include_consumed=False, limit=20)
    except Exception:
        pass

    if pending_briefs:
        labels = [
            (f"{p['created_at'].strftime('%Y-%m-%d %H:%M') if p['created_at'] else '시간미상'}"
             f" · 주문 {p['cart_count']}건")
            for p in pending_briefs
        ]
        id_by_label = {lbl: p['id'] for lbl, p in zip(labels, pending_briefs)}
        default_label = labels[0]
        if brief_id_t2:
            match = next((lbl for lbl, pid in id_by_label.items() if pid == brief_id_t2), None)
            if match:
                default_label = match

        sel_label = st.selectbox(
            "작업 내역 선택",
            options=labels,
            index=labels.index(default_label),
            help="① 단계에서 만들어진 작업 중 송장 업로드가 미완료인 것 (최근 순)",
        )
        sel_id = id_by_label[sel_label]
        if sel_id != brief_id_t2 or brief_bytes_t2 is None:
            try:
                content, fname = qgen.load_pending_brief(sel_id)
                st.session_state['qoo10_brief_bytes'] = content
                st.session_state['qoo10_brief_name'] = fname
                st.session_state['qoo10_brief_id'] = sel_id
            except Exception as ex:
                st.error(f"작업 내역 로드 실패: {ex}")
    else:
        st.error("⚠️ 미완료 작업이 없습니다. ① 단계에서 먼저 작업을 시작하세요.")

    oms_file = st.file_uploader(
        "KSE OMS 주문(출고&입고) 내역.xlsx 업로드",
        type=['xlsx'], key="oms_waybill_xlsx",
        help="KSE OMS에서 내려받은 주문 번호 ↔ 운송장 번호 자료 (취소건 자동 제외)",
    )
    if oms_file is not None:
        st.session_state['oms_bytes'] = oms_file.getvalue()
        st.session_state['oms_name'] = oms_file.name

    st.markdown(
        "<div style='font-size:0.75em'>\n\n"
        "| 구분 | 취합 경로 | 취합 |\n"
        "|------|----------|:----:|\n"
        f"| KSE OMS 주문(출고&입고) 내역 | KSE JP OMS > OMS > 주문관리 > 주문(출고&입고) - B2C > 엑셀다운 | "
        f"{'✅' if st.session_state.get('oms_bytes') else ''} |\n\n"
        "</div>",
        unsafe_allow_html=True,
    )

    if st.session_state.get('oms_bytes') and st.session_state.get('qoo10_brief_bytes'):
        st.success("✅ KSE OMS 파일 업로드 완료. 다음 단계로 진행하세요.")
        if st.button("다음 단계 →", key="goto_step5", type="primary"):
            st.session_state['qoo10_step'] = 5
            st.rerun()


def _step5_qsm_waybill_register():
    st.markdown("#### ⑤ QSM 송장 파일 생성")
    st.caption("아래 brief 파일을 다운로드하여 QSM 송장번호 등록 화면에 업로드하세요.")

    brief_bytes_t2 = st.session_state.get('qoo10_brief_bytes')
    brief_name_t2 = st.session_state.get('qoo10_brief_name')
    brief_id_t2 = st.session_state.get('qoo10_brief_id')
    oms_bytes_t4 = st.session_state.get('oms_bytes')

    if not brief_bytes_t2:
        st.error("⚠️ ④ 단계에서 작업 내역을 먼저 선택하세요.")
        if st.button("← ④ 단계로 이동"):
            st.session_state['qoo10_step'] = 4
            st.rerun()
        return
    if not oms_bytes_t4:
        st.error("⚠️ ④ 단계에서 KSE OMS 주문(출고&입고) 내역 파일을 먼저 업로드하세요.")
        if st.button("← ④ 단계로 이동"):
            st.session_state['qoo10_step'] = 4
            st.rerun()
        return

    try:
        brief_rows = qgen.parse_qsm_csv(brief_bytes_t2)
        cart_nos = [r.get('장바구니번호', '') for r in brief_rows]

        oms_map = qgen.parse_kse_oms_waybill(oms_bytes_t4)
        waybill_map = {c: oms_map[c] for c in cart_nos if c in oms_map}

        unhandled = len(cart_nos) - len(waybill_map)

        pending_briefs_t4 = []
        try:
            pending_briefs_t4 = qgen.list_pending_briefs(include_consumed=False, limit=20)
        except Exception:
            pass
        sel_meta = next((p for p in pending_briefs_t4 if p['id'] == brief_id_t2), None)
        expected_carts = sel_meta['cart_count'] if sel_meta else len(cart_nos)

        try:
            mappings_live = qgen.load_mappings()
            live_disabled = qgen.count_disabled_in_brief(brief_rows, mappings_live)
        except Exception:
            live_disabled = 0
        saved_disabled = sel_meta['disabled_count'] if sel_meta else 0
        expected_disabled = max(live_disabled, saved_disabled)

        expected_oms_orders = expected_carts - expected_disabled
        kse_issue = max(0, unhandled - expected_disabled)

        def _mark(ok: bool) -> str:
            return "✅" if ok else "⚠️"

        qsm_match = (len(cart_nos) == expected_carts)
        no_kse_issue = (kse_issue == 0)
        waybill_full = (len(waybill_map) == expected_oms_orders)

        c1, c2, c3 = st.columns(3)
        c1.metric("QSM 주문개수", f"{len(cart_nos)} {_mark(qsm_match)}")
        c2.metric(
            "KSE 미취급 주문개수",
            f"{unhandled} {_mark(no_kse_issue)}",
            help=f"이 중 ① 단계 취급안함: {expected_disabled}건 (예정) · KSE 쪽 이슈: {kse_issue}건",
        )
        c3.metric("KSE 송장개수", f"{len(waybill_map)} {_mark(waybill_full)}")

        if kse_issue > 0:
            missing = [c for c in cart_nos if c not in waybill_map]
            st.warning(
                f"KSE 쪽 이슈 **{kse_issue}건** (취급안함 {expected_disabled}건 외 추가). "
                f"전체 미매칭 목록: {', '.join(missing)}"
            )
        if not qsm_match:
            st.warning(
                f"① 주문 {expected_carts}건 ↔ 현재 brief {len(cart_nos)}건 불일치 "
                "(brief 파일이 변경됐을 가능성)."
            )

        if not waybill_map:
            st.error("매칭되는 송장번호가 없습니다. 파일을 다시 확인해주세요.")
            return

        try:
            qgen.update_outbound_waybills(waybill_map)
        except Exception as ex:
            st.warning(f"DB 갱신 실패 (등록은 진행 가능): {ex}")

        api_available = qapi.has_credentials()
        st.markdown("---")
        mode = st.radio(
            "등록 방식",
            options=(["API로 자동 등록 (권장)", "CSV 다운로드 (수동 업로드)"]
                     if api_available else ["CSV 다운로드 (수동 업로드)"]),
            horizontal=True,
            key="step5_mode",
            help=None if api_available else
                 "Qoo10 API 자격증명이 등록되지 않아 자동 등록 비활성화됨",
        )

        if mode == "API로 자동 등록 (권장)":
            order_pairs = []
            seen_orders = set()
            for r in brief_rows:
                cart = (r.get('장바구니번호') or '').strip()
                order = (r.get('주문번호') or '').strip()
                wb = waybill_map.get(cart)
                if wb and order and order not in seen_orders:
                    order_pairs.append((order, wb))
                    seen_orders.add(order)

            st.info(
                f"📤 등록 대상: 주문 **{len(order_pairs)}건**, "
                f"송장 **{len(set(w for _, w in order_pairs))}개** "
                f"(같은 장바구니의 주문은 같은 송장)"
            )

            if st.button("🚀 QSM에 자동 등록", key="api_register",
                         type="primary", width="stretch",
                         disabled=not order_pairs):
                with st.spinner(f"{len(order_pairs)}건 등록 중..."):
                    try:
                        sak = qapi.get_sak()
                        results = qapi.register_waybills_batch(sak, order_pairs)
                    except Exception as ex:
                        st.error(f"인증/통신 실패: {ex}")
                        results = []

                if results:
                    ok_n = sum(1 for r in results if r['ok'])
                    fail_n = len(results) - ok_n
                    cc1, cc2 = st.columns(2)
                    cc1.metric("등록 성공", ok_n)
                    cc2.metric("실패", fail_n,
                               delta=None if fail_n == 0 else f"-{fail_n}",
                               delta_color="inverse")

                    df_result = pd.DataFrame([{
                        '주문번호': r['order_no'],
                        '송장번호': r['tracking_no'],
                        '결과': '✅ 성공' if r['ok'] else f"❌ {r.get('msg', '실패')}",
                    } for r in results])
                    st.dataframe(df_result, hide_index=True, width="stretch")

                    if fail_n == 0:
                        st.success("🎉 전체 송장 등록 완료. "
                                   "QSM에서 주문 상태가 '배송중'으로 변경되었습니다.")
                        if brief_id_t2:
                            try:
                                qgen.mark_brief_consumed(brief_id_t2)
                            except Exception:
                                pass
                        if st.button("🏁 작업 종료", key="finish_step5_api",
                                     type="primary", width="stretch"):
                            for k in ('qoo10_detail_bytes', 'qoo10_detail_name',
                                      'qoo10_brief_bytes', 'qoo10_brief_name',
                                      'qoo10_brief_id', 'oms_bytes', 'oms_name',
                                      'qoo10_api_orders'):
                                st.session_state.pop(k, None)
                            st.session_state['qoo10_step'] = 1
                            st.rerun()
                    else:
                        st.warning(
                            "일부 실패. 실패 건은 'CSV 다운로드' 모드로 전환해 "
                            "수동 업로드하거나 KSE 송장번호를 재확인하세요."
                        )
        else:
            csv_bytes, _missing = qgen.build_qsm_waybill_csv(brief_bytes_t2, waybill_map)
            base_name = brief_name_t2 or "QSM_waybill.csv"
            out_name = f"(송장번호 입력됨) {base_name}"
            st.download_button(
                f"📥 {out_name} 다운로드",
                data=csv_bytes,
                file_name=out_name,
                mime="text/csv",
                width="stretch",
                type="primary",
            )
            if st.button("다음 단계 →", key="goto_step6", type="primary"):
                st.session_state['qoo10_step'] = 6
                st.rerun()
    except Exception as e:
        st.error(f"처리 중 오류: {e}")


def _step6_qsm_register_guide():
    st.markdown("#### ⑥ QSM 송장 등록")
    st.caption("앞 단계에서 다운로드한 송장 brief 파일을 QSM에 업로드하는 방법 안내.")

    st.info(
        "📌 **QSM 송장 업로드 경로**  \n"
        "_경로 정보는 추후 추가 예정_"
    )
    st.markdown("> _상세 안내(스크린샷)는 추후 추가 예정._")

    brief_id_t6 = st.session_state.get('qoo10_brief_id')
    if st.button("✅ 작업 완료", key="finish_step6", type="primary", width="stretch"):
        try:
            if brief_id_t6:
                qgen.mark_brief_consumed(brief_id_t6)
            for k in ('qoo10_detail_bytes', 'qoo10_detail_name',
                      'qoo10_brief_bytes', 'qoo10_brief_name',
                      'qoo10_brief_id', 'oms_bytes', 'oms_name'):
                st.session_state.pop(k, None)
            st.session_state['qoo10_step'] = 1
            st.success("작업 완료처리됨")
            st.rerun()
        except Exception as ex:
            st.error(f"실패: {ex}")


def _render_history_tab():
    st.markdown("**출고요청서 생성 이력 + 송장번호 추적**")
    st.caption("Outbound 생성 시 자동 저장, QSM 송장 업로드 시 송장번호 자동 갱신.")

    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        wb_filter = st.selectbox(
            "송장 상태", ["전체", "송장 있음", "송장 없음"], index=0
        )
    with col_f2:
        days_filter = st.number_input("최근 N일", min_value=1, max_value=365, value=30, step=1)
    with col_f3:
        search_hist = st.text_input("🔍 검색 (장바구니/송장/수취인)")

    conds = ["generated_at >= ((CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul') - INTERVAL '%s days')" % int(days_filter)]
    params = []
    if wb_filter == "송장 있음":
        conds.append("waybill IS NOT NULL AND waybill != ''")
    elif wb_filter == "송장 없음":
        conds.append("(waybill IS NULL OR waybill = '')")
    if search_hist:
        conds.append("(qoo10_cart_no ILIKE %s OR waybill ILIKE %s OR recipient ILIKE %s)")
        p = f"%{search_hist}%"
        params += [p, p, p]

    where = " AND ".join(conds)
    try:
        df_hist = pg.query_df(f"""
            SELECT generated_at, qoo10_cart_no, qoo10_order_no, sku_code, sku_name,
                   planned_qty, recipient, postal_code, address, waybill, waybill_updated_at,
                   qoo10_product_name, qoo10_option, source_file
            FROM qoo10_outbound
            WHERE {where}
            ORDER BY generated_at DESC, qoo10_cart_no, sku_code
            LIMIT 500
        """, params)
    except Exception as ex:
        st.error(f"이력 조회 실패: {ex}")
        return

    c1, c2, c3 = st.columns(3)
    total_rows = len(df_hist)
    with_wb = int((df_hist['waybill'].notna() & (df_hist['waybill'] != '')).sum()) if not df_hist.empty else 0
    c1.metric("조회 행수", total_rows)
    c2.metric("송장 확보", with_wb)
    c3.metric("송장 대기", total_rows - with_wb)

    if df_hist.empty:
        st.info("조건에 맞는 이력이 없습니다.")
        return

    st.dataframe(
        df_hist.rename(columns={
            'generated_at': '생성시각',
            'qoo10_cart_no': '장바구니번호',
            'qoo10_order_no': 'QSM주문번호',
            'sku_code': 'SKU코드',
            'sku_name': 'SKU상품명',
            'planned_qty': '수량',
            'recipient': '수취인',
            'postal_code': '우편번호',
            'address': '주소',
            'waybill': '송장번호',
            'waybill_updated_at': '송장갱신시각',
            'qoo10_product_name': 'Qoo10상품',
            'qoo10_option': 'Qoo10옵션',
            'source_file': '원본파일',
        }),
        width="stretch", hide_index=True,
    )

    csv_export = df_hist.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        "📥 조회 결과 CSV 다운로드", data=csv_export,
        file_name=f"qoo10_outbound_history_{datetime.date.today().strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )


def _render_mapping_tab():
    st.markdown("Qoo10 상품/옵션 ↔ KSE SKU 매핑 — **상단 요약 / 하단 편집**")
    st.caption("각 행 = 하나의 매핑. SKU 구성 컬럼에 세트 포함 전체 품목이 요약되어 표시됩니다.")

    sku_catalog = qgen.load_kse_sku_catalog()
    sku_name_to_code = {s['sku_name']: s['sku_code'] for s in sku_catalog}
    sku_name_options = list(sku_name_to_code.keys())

    if not sku_catalog:
        st.warning(
            "KSE SKU 카탈로그가 비어있습니다 (이력 없음). "
            "기존 매핑을 보고/수정하는 데는 문제가 없으나 새 SKU는 첫 출고 후 자동 등록됩니다. "
            "또는 직접 SKU 코드/명을 입력하려면 DB 콘솔에서 직접 행을 삽입하세요."
        )

    try:
        maps_df = pg.query_df("""
            SELECT qoo10_name, qoo10_option, item_codes, sku_codes, quantities, enabled
            FROM qoo10_product_mapping ORDER BY enabled DESC, qoo10_name, qoo10_option
        """)
    except Exception as ex:
        st.error(f"매핑 조회 실패: {ex}")
        return

    summary_rows = []
    for _, row in maps_df.iterrows():
        names = [n.strip() for n in (row['item_codes'] or '').split(',') if n.strip()]
        qtys = [q.strip() for q in (row['quantities'] or '').split(',') if q.strip()]
        if len(qtys) < len(names):
            qtys += ['1'] * (len(names) - len(qtys))
        sku_summary = ' + '.join(f"{n}×{q}" for n, q in zip(names, qtys))
        summary_rows.append({
            'Qoo10 상품명': row['qoo10_name'],
            'Qoo10 옵션': row['qoo10_option'] or '',
            'SKU 구성': sku_summary,
            '품목수': len(names),
            '활성': '✅' if row['enabled'] else '⏸️',
        })
    summary_df = pd.DataFrame(summary_rows)

    search = st.text_input("🔍 검색", placeholder="상품명 또는 옵션의 일부 (공백시 전체)")
    filtered = summary_df
    if search and not summary_df.empty:
        mask = summary_df['Qoo10 상품명'].str.contains(search, case=False, na=False) | \
               summary_df['Qoo10 옵션'].str.contains(search, case=False, na=False)
        filtered = summary_df[mask]

    st.caption(f"총 {len(summary_df)}개 매핑" + (f" · 필터 결과 {len(filtered)}개" if search else ""))

    st.dataframe(
        filtered, width="stretch", hide_index=True,
        column_config={
            'Qoo10 상품명': st.column_config.TextColumn(width="large"),
            'Qoo10 옵션': st.column_config.TextColumn(width="medium"),
            'SKU 구성': st.column_config.TextColumn(width="large"),
            '품목수': st.column_config.NumberColumn(width="small"),
            '활성': st.column_config.TextColumn(width="small"),
        },
    )

    st.markdown("---")
    st.markdown("### ✏️ 매핑 편집")

    mapping_keys = [(row['qoo10_name'], row['qoo10_option'] or '') for _, row in maps_df.iterrows()]
    options = ['— 새 매핑 추가 —'] + [
        f"{qn[:50]}{'...' if len(qn)>50 else ''}  /  {qo[:40] if qo else '(옵션없음)'}"
        for qn, qo in mapping_keys
    ]
    sel_idx = st.selectbox(
        "편집할 매핑 선택", options=range(len(options)),
        format_func=lambda i: options[i], key="sel_mapping_idx",
    )

    if sel_idx == 0:
        edit_qn = st.text_area("Qoo10 상품명", value="", height=80, key="edit_qn_new")
        edit_qo = st.text_input("Qoo10 옵션 (없으면 빈칸)", value="", key="edit_qo_new")
        edit_enabled = st.checkbox("활성", value=True, key="edit_en_new")
        init_skus = [(sku_name_options[0], 1)] if sku_name_options else []
        is_new = True
        orig_key = None
    else:
        qn, qo = mapping_keys[sel_idx - 1]
        src_row = maps_df.iloc[sel_idx - 1]
        st.markdown(f"**Qoo10 상품명**: `{qn}`")
        st.markdown(f"**Qoo10 옵션**: `{qo or '(없음)'}`")
        edit_qn = qn
        edit_qo = qo
        edit_enabled = st.checkbox("활성", value=bool(src_row['enabled']), key=f"edit_en_{sel_idx}")
        names = [n.strip() for n in (src_row['item_codes'] or '').split(',') if n.strip()]
        qtys = [int(q) for q in (src_row['quantities'] or '').split(',') if q.strip()]
        init_skus = list(zip(
            [n if n in sku_name_to_code else (sku_name_options[0] if sku_name_options else n) for n in names],
            qtys if qtys else [1] * len(names),
        ))
        is_new = False
        orig_key = (qn, qo)

    st.markdown("**KSE SKU 구성** (세트면 `+ 행 추가`로 여러 품목)")

    if not sku_name_options:
        st.info("선택 가능한 KSE 품목이 없습니다 (이력 비어있음). 직접 매핑이 필요하면 DB에서 `qoo10_product_mapping` 행을 수기로 입력하세요.")
        return

    sku_init_df = pd.DataFrame({
        'KSE 품목': [s[0] for s in init_skus] or [sku_name_options[0]],
        '수량': [s[1] for s in init_skus] or [1],
    })
    sku_editor_key = f"sku_editor_{sel_idx}"
    sku_edited = st.data_editor(
        sku_init_df,
        column_config={
            'KSE 품목': st.column_config.SelectboxColumn(
                options=sku_name_options, required=True, width="large",
                help="품목 선택 시 SKU 코드는 자동 매칭"),
            '수량': st.column_config.NumberColumn(
                min_value=1, step=1, default=1, required=True, width="small"),
        },
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        key=sku_editor_key,
    )

    btn_cols = st.columns([1, 1, 4])
    with btn_cols[0]:
        do_save = st.button(
            "➕ 추가" if is_new else "💾 저장",
            type="primary", width="stretch", key=f"save_btn_{sel_idx}"
        )
    with btn_cols[1]:
        do_delete = False
        if not is_new:
            do_delete = st.button("🗑 삭제", width="stretch", key=f"del_btn_{sel_idx}")

    if do_save:
        qn = str(edit_qn or '').strip()
        qo = str(edit_qo or '').strip()
        if not qn:
            st.error("Qoo10 상품명은 필수입니다.")
        else:
            valid = sku_edited.dropna(subset=['KSE 품목'])
            if valid.empty:
                st.error("최소 1개 품목이 필요합니다.")
            else:
                payload = []
                bad = []
                for i, r in valid.iterrows():
                    name = r['KSE 품목']
                    if name not in sku_name_to_code:
                        bad.append(f"행 {i+1}: {name}")
                        continue
                    payload.append((sku_name_to_code[name], name, int(r['수량'] or 1)))
                if bad:
                    st.error("품목 오류:\n" + "\n".join(bad))
                else:
                    try:
                        if orig_key and (qn, qo) != orig_key:
                            qgen.delete_mapping(*orig_key)
                        qgen.add_mapping(qn, qo, payload, enabled=edit_enabled)
                        st.success("저장됨")
                        st.rerun()
                    except Exception as ex:
                        st.error(f"실패: {ex}")

    if do_delete and orig_key:
        try:
            qgen.delete_mapping(*orig_key)
            st.success("삭제됨")
            st.rerun()
        except Exception as ex:
            st.error(f"실패: {ex}")


def render_page():
    """Qoo10 일본 출고 메인 렌더러. dashboard.py에서 채널 선택 시 호출."""
    render_credentials_sidebar()

    tab_main, tab_history, tab_mapping = st.tabs([
        "📤 출고요청", "📚 출고 이력", "🔧 상품 매핑"
    ])

    with tab_main:
        active_step = int(st.session_state.get('qoo10_step', 1))
        _render_stepper(active_step)
        st.markdown("---")
        if active_step == 1:
            _step1_qsm_collect()
        elif active_step == 2:
            _step2_outbound_generate()
        elif active_step == 3:
            _step3_oms_upload_guide()
        elif active_step == 4:
            _step4_collect_waybills()
        elif active_step == 5:
            _step5_qsm_waybill_register()
        elif active_step == 6:
            _step6_qsm_register_guide()

    with tab_history:
        _render_history_tab()

    with tab_mapping:
        _render_mapping_tab()
