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
from utils.timezone import kst_today
from qoo10 import api_client as qapi
from qoo10 import generator as qgen
from qoo10 import kse_client as ksec


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


def render_kse_credentials_sidebar():
    """KSE JP OMS 자격증명 사이드바 expander (QSM 과 동일 패턴)."""
    with st.sidebar.expander("🔐 KSE OMS 자격증명", expanded=False):
        status = ksec.get_credentials_status()
        if status['configured']:
            _urk = status.get('urkey') or ''
            st.success(f"✅ 자격증명 등록됨 (계정: `{_urk}`, source: {status.get('source')})")
            if status.get('updated_at'):
                st.caption(f"마지막 갱신: `{status['updated_at']}`")
        else:
            st.warning("⚠️ 자격증명 미등록 — KSE OMS 자동 수집을 사용하려면 아래 저장")

        st.caption("입력란을 비워두면 기존 값이 유지됩니다 (부분 갱신 가능)")

        urkey_in = st.text_input(
            "계정 (urkey)", placeholder="katchers",
            key="kse_sb_urkey",
        )
        pw_in = st.text_input(
            "비밀번호", type="password", key="kse_sb_password",
        )
        with st.expander("고급 (화주코드/물류그룹)", expanded=False):
            ctkey_in = st.text_input(
                "ctkey (화주코드, 기본 KE00003)", placeholder="KE00003",
                key="kse_sb_ctkey",
            )
            loggrpcd_in = st.text_input(
                "loggrpcd (물류그룹, 기본 1)", placeholder="1",
                key="kse_sb_loggrpcd",
            )

        b1, b2 = st.columns(2)
        with b1:
            if st.button("💾 저장", key="kse_sb_save", width="stretch", type="primary"):
                ok = ksec.save_credentials_to_db(
                    urkey=urkey_in.strip() or None,
                    password=pw_in.strip() or None,
                    ctkey=ctkey_in.strip() or None,
                    loggrpcd=loggrpcd_in.strip() or None,
                )
                if ok:
                    st.success("저장 완료")
                    st.rerun()
                else:
                    st.error("저장 실패 (DB 연결 확인)")
        with b2:
            if st.button("🧪 연결 테스트", key="kse_sb_test", width="stretch",
                         help="저장된 자격증명으로 로그인 시도"):
                # 입력란 값이 있으면 세션에 저장하지 않고 직접 시도할 수 있으나,
                # KSE 는 여러 필드 조합이 있어 저장 후 테스트가 안전. 우선 저장된 값 기준 로그인.
                result = ksec.test_login()
                if result['ok']:
                    st.success(f"✅ {result['message']} (JWT {result['jwt_len']} chars)")
                else:
                    st.error(f"❌ {result['message']}")


def _step2_outbound_generate():
    st.markdown("#### ① 일본 KSE 출고요청서 생성")
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

        _missing_raw = [e for e in errors if e['원인'] == '상품 매핑 없음']
        disabled_errors = [e for e in errors if e['원인'] == '매핑 비활성(취급 안함)']
        # 신 채널별 매핑 모델: qoo10_japan 매핑이 없어도 국내(cachers_qoo10_kr)
        # 채널에 활성 매핑이 있으면 '신규 매핑 필요'가 아니라 국내 출고분이다
        # (탭1 분류와 일치시킴 — 레거시 enabled=False 국내판정 대체).
        from db import mapping as _m_kr
        _kr_active = _m_kr.load_for_channel('cachers_qoo10_kr', active_only=True)
        missing_errors = []
        for _e in _missing_raw:
            if (_e['상품명'], _e['옵션정보']) in _kr_active:
                disabled_errors.append({**_e, '원인': '국내 출고(KR 채널)'})
            else:
                missing_errors.append(_e)

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
                                    qgen.upsert_both_channels(
                                        qgen.CHANNEL_QOO10_JAPAN,
                                        e['상품명'], e['옵션정보'], skus_payload,
                                    )
                                    st.success(
                                        "매핑 저장 완료 (일본 활성 + 국내 비활성 동시 등록): "
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

        # KR 채널 활성건은 재분류되어 missing_errors 에서 빠짐 — 가드도 동일 기준
        mapping_complete = not missing_errors

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
                              'qoo10_brief_id', 'oms_bytes', 'oms_name', 'oms_waybill_map'):
                        st.session_state.pop(k, None)
                    st.session_state['qoo10_step'] = 1
                    st.success("작업 종료 처리됨")
                    st.rerun()
                except Exception as ex:
                    st.error(f"작업 종료 실패: {ex}")
        elif out_rows:
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
                today_str = kst_today().strftime('%Y%m%d')
                ob_name = f"Outbound_ship_conf_btoc_{today_str}.xlsx"
                try:
                    n_saved = qgen.save_outbound_log(
                        rows, out_rows, mappings, det_name or 'unknown.csv'
                    )
                    st.caption(f"🗂 출고 이력 DB 기록: {n_saved}건")
                except Exception as ex:
                    st.warning(f"DB 기록 실패 (다운로드는 가능): {ex}")
                if bid_now:
                    if qgen.save_brief_outbound(bid_now, xlsx_bytes, ob_name):
                        st.caption("💾 출고요청서 저장됨 — 이어서 모드에서 재다운로드 가능")
                st.download_button(
                    f"📥 {ob_name} 다운로드",
                    data=xlsx_bytes,
                    file_name=ob_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch",
                    type="primary",
                )
                st.info(
                    "📤 **일본 KSE 출고요청서 (Outbound_ship_conf~) 다운로드 후 "
                    "일본 KSE OMS에 업로드 해주세요.**  \n"
                    "업로드 경로: **일본 KSE OMS > 주문관리 > 주문업로드**"
                )
    except Exception as e:
        st.error(f"처리 중 오류: {e}")


def _step3_oms_upload_guide():
    st.markdown("#### ② KSE 출고요청서 등록")
    st.caption("앞 단계에서 다운로드한 출고요청서를 KSE OMS에 업로드하는 방법 안내.")

    st.info(
        "📌 **일본 KSE OMS 업로드 경로**  \n"
        "**일본 KSE OMS > 주문관리 > 주문업로드**"
    )
    st.markdown("> _상세 안내(스크린샷)는 추후 추가 예정._")


def _step4_collect_waybills():
    st.markdown("#### ③ KSE 송장번호 취합")
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

    # ── 자동 수집 (KSE OMS API 직결) ──────────────────────────
    auto_map = st.session_state.get('oms_waybill_map')
    auto_label = (
        f"🤖 KSE에서 자동 수집 ({len(auto_map)}건 매핑됨) — 재수집" if auto_map
        else "🤖 KSE에서 자동 수집 (수동 xlsx 업로드 대체)"
    )
    with st.expander(auto_label, expanded=not (auto_map or st.session_state.get('oms_bytes'))):
        st.caption(
            "KSE OMS 에 API 로 로그인해 주문(출고&입고) 데이터에서 송장번호를 직접 수집합니다. "
            "자격증명은 `.streamlit/secrets.toml` 의 `[kse_jp]` 섹션 (urkey, password) 을 사용."
        )
        _today = kst_today()
        col_a, col_b, col_c = st.columns([1, 1, 1])
        with col_a:
            auto_start = st.date_input(
                "출고예정일 시작", value=_today - datetime.timedelta(days=1),
                key="kse_auto_start",
            )
        with col_b:
            auto_end = st.date_input(
                "출고예정일 종료", value=_today + datetime.timedelta(days=1),
                key="kse_auto_end",
            )
        with col_c:
            st.write("")
            st.write("")
            run_auto = st.button("자동 수집 실행", type="primary", key="kse_auto_run")
        if run_auto:
            try:
                with st.spinner("KSE OMS 로그인 · 조회 중..."):
                    mapping = ksec.fetch_waybills(auto_start, auto_end)
                if not mapping:
                    st.warning(f"매핑 결과 0건. 조건을 확인하세요 ({auto_start} ~ {auto_end}).")
                else:
                    st.session_state['oms_waybill_map'] = mapping
                    st.session_state.pop('oms_bytes', None)
                    st.session_state.pop('oms_name', None)
                    st.success(f"✅ 자동 수집 완료 — {len(mapping)}건 송장 매핑")
                    st.rerun()
            except ksec.KseClientError as ex:
                st.error(f"KSE 자동 수집 실패: {ex}")
            except Exception as ex:
                st.error(f"KSE 자동 수집 예외: {ex}")
        if auto_map:
            st.markdown(f"**📋 수집 결과 — 총 {len(auto_map)}건**")
            st.dataframe(
                pd.DataFrame(
                    list(auto_map.items()),
                    columns=["장바구니번호(externorderkey)", "송장번호(waybillno)"],
                ),
                width="stretch", hide_index=True,
            )

    oms_file = st.file_uploader(
        "KSE OMS 주문(출고&입고) 내역.xlsx 업로드 (수동)",
        type=['xlsx'], key="oms_waybill_xlsx",
        help="KSE OMS에서 내려받은 주문 번호 ↔ 운송장 번호 자료 (취소건 자동 제외). 자동 수집이 실패했을 때만 사용.",
    )
    if oms_file is not None:
        st.session_state['oms_bytes'] = oms_file.getvalue()
        st.session_state['oms_name'] = oms_file.name
        st.session_state.pop('oms_waybill_map', None)

    st.markdown(
        "<div style='font-size:0.75em'>\n\n"
        "| 구분 | 취합 경로 | 취합 |\n"
        "|------|----------|:----:|\n"
        f"| KSE OMS 주문(출고&입고) 내역 | KSE JP OMS > OMS > 주문관리 > 주문(출고&입고) - B2C > 엑셀다운 | "
        f"{'✅' if (st.session_state.get('oms_bytes') or st.session_state.get('oms_waybill_map')) else ''} |\n\n"
        "</div>",
        unsafe_allow_html=True,
    )

    if (st.session_state.get('oms_bytes') or st.session_state.get('oms_waybill_map')) \
            and st.session_state.get('qoo10_brief_bytes'):
        st.success("✅ 일본 KSE OMS 송장 데이터 준비 완료. 아래로 진행하세요.")


def _step5_qsm_waybill_register():
    st.markdown("#### ④ QSM 송장 파일 생성")
    st.caption("아래 brief 파일을 다운로드하여 QSM 송장번호 등록 화면에 업로드하세요.")

    brief_bytes_t2 = st.session_state.get('qoo10_brief_bytes')
    brief_name_t2 = st.session_state.get('qoo10_brief_name')
    brief_id_t2 = st.session_state.get('qoo10_brief_id')
    oms_bytes_t4 = st.session_state.get('oms_bytes')
    oms_waybill_map = st.session_state.get('oms_waybill_map')

    if not brief_bytes_t2:
        st.error("⚠️ ④ 단계에서 작업 내역을 먼저 선택하세요.")
        if st.button("← ④ 단계로 이동"):
            st.session_state['qoo10_step'] = 4
            st.rerun()
        return
    if not oms_bytes_t4 and not oms_waybill_map:
        st.error("⚠️ ④ 단계에서 KSE OMS 자동 수집 또는 xlsx 업로드를 먼저 수행하세요.")
        if st.button("← ④ 단계로 이동"):
            st.session_state['qoo10_step'] = 4
            st.rerun()
        return

    try:
        brief_rows = qgen.parse_qsm_csv(brief_bytes_t2)
        cart_nos = [r.get('장바구니번호', '') for r in brief_rows]

        if oms_waybill_map:
            oms_map = oms_waybill_map
        else:
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
                                      'oms_waybill_map', 'qoo10_api_orders'):
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
    except Exception as e:
        st.error(f"처리 중 오류: {e}")


def _step6_qsm_register_guide():
    st.markdown("#### ⑤ QSM 송장 등록")
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
                      'qoo10_brief_id', 'oms_bytes', 'oms_name', 'oms_waybill_map'):
                st.session_state.pop(k, None)
            st.session_state['qoo10_step'] = 1
            st.success("작업 완료처리됨")
            st.rerun()
        except Exception as ex:
            st.error(f"실패: {ex}")


