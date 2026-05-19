"""탭1(신규주문 처리) 공용 — 미매핑 신규 상품을 일본·국내 양쪽 채널에 한 번에 등록.

큐텐-일본/국내 탭1 둘 다 동일 분류(`_classify`)로 `unknown_orders` 를 산출하므로,
신규 상품 매핑을 탭2가 아니라 탭1에서 바로(양쪽 채널 동시) 처리하도록 공용화.

요구사항: 모든 큐텐 상품은 qoo10_japan·cachers_qoo10_kr 양쪽에 매핑이 존재해야
함(한 채널 등록했다고 다른 채널 생략 금지). 단 채널별로 SKU 구성·활성여부를
**독립적으로** 입력/설정한다(일본 SKU=KSE / 국내 SKU=다원 으로 다를 수 있음).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from db import mapping as _m
from qoo10 import generator as _qgen


# (라벨, 채널키, 기본 활성여부) — 큐텐 대부분 일본 출고라 일본 기본 활성
_CHANNELS = [
    ("🇯🇵 큐텐-일본", _qgen.CHANNEL_QOO10_JAPAN, True),
    ("🇰🇷 큐텐-국내", _qgen.CHANNEL_CACHERS_QOO10_KR, False),
]


def _empty_sku_df() -> pd.DataFrame:
    return pd.DataFrame({'SKU 코드': [''], '상품명': [''], '수량': [1]})


def _collect_payload(edited: pd.DataFrame) -> list[tuple[str, str, int]]:
    valid = edited[edited['SKU 코드'].astype(str).str.strip() != '']
    out: list[tuple[str, str, int]] = []
    for _, r in valid.iterrows():
        code = str(r['SKU 코드']).strip()
        nm = str(r['상품명'] or '').strip() or code
        qty = int(r['수량'] or 1)
        out.append((code, nm, qty))
    return out


def render_unknown_inline_mapping(unknown_orders: list) -> None:
    """미매핑 신규 상품(상품명,옵션) 단위로 **채널별 독립** SKU 구성 + 활성여부
    입력 → 일본·국내 양쪽 채널에 동시 등록.

    - 채널별 data_editor / 활성 체크박스를 분리(키 독립) → 한 채널 입력이
      다른 채널 입력으로 덮이지 않음.
    - 위젯 key 는 (상품명,옵션) 시그니처 기반 — 목록 순서가 바뀌어도 안정.
    """
    if not unknown_orders:
        return

    seen: dict[tuple, dict] = {}
    for q in unknown_orders:
        k = ((q.get('상품명') or '').strip(), (q.get('옵션정보') or '').strip())
        seen.setdefault(k, q)
    items = list(seen.keys())

    st.markdown("---")
    st.markdown(f"#### 🆕 신규 상품 매핑 ({len(items)}건) — 일본·국내 양쪽 등록")
    st.caption(
        "채널별로 SKU 구성과 **활성 여부**를 각각 설정하세요(일본=KSE / 국내=다원 "
        "SKU 가 다를 수 있음). 양쪽 채널 모두 매핑이 있어야 하며, 실제 출고는 "
        "활성(active)인 채널로 나갑니다."
    )

    for qname, qoption in items:
        sig = abs(hash((qname, qoption)))
        with st.container(border=True):
            st.markdown(f"**{qname}**")
            st.caption(f"옵션: `{qoption or '(없음)'}`")

            ch_cols = st.columns(len(_CHANNELS))
            editors: dict[str, pd.DataFrame] = {}
            actives: dict[str, bool] = {}
            for col, (label, ch, default_active) in zip(ch_cols, _CHANNELS):
                with col:
                    st.markdown(f"**{label}**")
                    actives[ch] = st.checkbox(
                        "활성(이 채널로 출고)",
                        value=default_active,
                        key=f"q1map_act_{ch}_{sig}",
                    )
                    editors[ch] = st.data_editor(
                        _empty_sku_df(),
                        column_config={
                            'SKU 코드': st.column_config.TextColumn(
                                required=True, width="medium",
                                help="세트면 행 추가"),
                            '상품명': st.column_config.TextColumn(
                                width="medium", help="비고 — 빈값이면 SKU 코드"),
                            '수량': st.column_config.NumberColumn(
                                min_value=1, step=1, default=1,
                                required=True, width="small"),
                        },
                        num_rows="dynamic",
                        hide_index=True,
                        width="stretch",
                        key=f"q1map_ed_{ch}_{sig}",
                    )

            if st.button(
                "💾 일본·국내 양쪽 채널 등록",
                type="primary", width="stretch",
                key=f"q1map_save_{sig}",
            ):
                payloads = {ch: _collect_payload(df) for ch, df in editors.items()}
                missing = [
                    lbl for (lbl, ch, _d) in _CHANNELS if not payloads[ch]
                ]
                if missing:
                    st.error(
                        f"SKU 구성 누락: {', '.join(missing)} — "
                        "양쪽 채널 모두 1개 이상 SKU 코드 필요(둘 다 매핑되어야 함)."
                    )
                else:
                    ok = True
                    for _lbl, ch, _d in _CHANNELS:
                        if not _m.upsert(ch, qname, qoption, payloads[ch],
                                         is_active=bool(actives[ch])):
                            ok = False
                    if ok:
                        _act = [lbl for (lbl, ch, _d) in _CHANNELS if actives[ch]]
                        st.success(
                            "양쪽 채널 등록 완료 (활성: "
                            + (", ".join(_act) if _act else "없음")
                            + "). 다시 가져오기/재분류 시 반영."
                        )
                        st.rerun()
                    else:
                        st.error("등록 실패 (DB 연결 / 한쪽 채널 실패).")
