"""탭1(신규주문 처리) 공용 — 미매핑 신규 상품을 일본·국내 양쪽 채널에 한 번에 등록.

큐텐-일본/국내 탭1 둘 다 동일 분류(`_classify`)로 `unknown_orders` 를 산출하므로,
신규 상품 매핑을 탭2가 아니라 탭1에서 바로(양쪽 채널 동시) 처리하도록 공용화.

요구사항: 모든 큐텐 상품은 qoo10_japan·cachers_qoo10_kr 양쪽에 동일 SKU 구성으로
존재해야 함. 출고 채널은 is_active 토글로 결정 → 여기서 선택한 채널만 활성,
반대 채널은 비활성으로 동시 생성 (한 채널 등록했다고 다른 채널 생략 금지).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from qoo10 import generator as _qgen


_ACTIVE_CHOICES = {
    "일본 출고 (큐텐-일본 활성)": _qgen.CHANNEL_QOO10_JAPAN,
    "국내 출고 (큐텐-국내 활성)": _qgen.CHANNEL_CACHERS_QOO10_KR,
}


def render_unknown_inline_mapping(unknown_orders: list) -> None:
    """미매핑 신규 상품(상품명,옵션) 단위로 SKU 구성 입력 → 양쪽 채널 동시 등록.

    선택한 '이번 출고 채널' = is_active=True, 반대 채널 = is_active=False.
    """
    if not unknown_orders:
        return

    seen: dict[tuple, dict] = {}
    for q in unknown_orders:
        k = ((q.get('상품명') or '').strip(), (q.get('옵션정보') or '').strip())
        seen.setdefault(k, q)
    items = list(seen.keys())

    st.markdown("---")
    st.markdown(f"#### 🆕 신규 상품 매핑 ({len(items)}건) — 일본·국내 양쪽 동시 등록")
    st.caption(
        "여기서 등록하면 큐텐-일본·국내 **양쪽 채널에 동일 SKU 구성**으로 매핑됩니다. "
        "선택한 출고 채널만 활성(is_active), 반대 채널은 비활성으로 생성 — "
        "추후 출고 채널 변경은 어드민 상품 매핑에서 토글."
    )

    for idx, (qname, qoption) in enumerate(items):
        with st.container(border=True):
            st.markdown(f"**[{idx + 1}/{len(items)}] {qname}**")
            st.caption(f"옵션: `{qoption or '(없음)'}`")

            sig = abs(hash((qname, qoption)))
            active_label = st.radio(
                "이번 출고 채널 (활성)",
                options=list(_ACTIVE_CHOICES.keys()),
                horizontal=True,
                key=f"q1map_active_{idx}_{sig}",
            )
            ed_key = f"q1map_ed_{idx}_{sig}"
            base = pd.DataFrame({'SKU 코드': [''], '상품명': [''], '수량': [1]})
            edited = st.data_editor(
                base,
                column_config={
                    'SKU 코드': st.column_config.TextColumn(
                        required=True, width="medium",
                        help="예) KC_8809885876166 / 세트면 행 추가"),
                    '상품명': st.column_config.TextColumn(
                        width="large", help="비고용 — 빈값이면 SKU 코드로 채워짐"),
                    '수량': st.column_config.NumberColumn(
                        min_value=1, step=1, default=1, required=True, width="small"),
                },
                num_rows="dynamic",
                hide_index=True,
                width="stretch",
                key=ed_key,
            )

            if st.button(
                "💾 일본·국내 양쪽 채널 등록",
                type="primary", width="stretch",
                key=f"q1map_save_{ed_key}",
            ):
                valid = edited[edited['SKU 코드'].astype(str).str.strip() != '']
                if valid.empty:
                    st.error("최소 1개 SKU 코드 필요.")
                else:
                    payload = []
                    for _, r in valid.iterrows():
                        code = str(r['SKU 코드']).strip()
                        nm = str(r['상품명'] or '').strip() or code
                        qty = int(r['수량'] or 1)
                        payload.append((code, nm, qty))
                    active_ch = _ACTIVE_CHOICES[active_label]
                    if _qgen.upsert_both_channels(active_ch, qname, qoption, payload):
                        st.success(
                            f"양쪽 채널 등록 완료 (활성: {active_label}): "
                            + " + ".join(f"{n}×{q}" for _, n, q in payload)
                            + " — 다시 가져오기/재분류 시 반영됨."
                        )
                        st.rerun()
                    else:
                        st.error("등록 실패 (DB 연결 / 한쪽 채널 실패).")
