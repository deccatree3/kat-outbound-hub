"""KSE OMS → QSM 자동 송장 등록 스크립트.

GH Actions cron 이 KST 13/15/17/19/21시 실행.

흐름:
  1. KSE OMS 로그인 → 검색 API → {장바구니번호(packNo): 송장번호} 매핑 획득
  2. DB 에 저장된 pending brief 중 아직 consumed 안 된 것 조회
  3. 각 brief 안 (orderNo, packNo) 을 KSE 매핑과 대조 → (orderNo, waybill) 페어 생성
  4. 대상 orderNo 의 QSM 현재 상태 조회 → 이미 배송중(ShippingStat=4) 이면 skip (중복 등록 방지)
  5. 남은 것 QSM SetSendingInfo API 로 등록
  6. brief 안 모든 orderNo 가 처리됐으면 consumed_at 마크
  7. Slack Webhook 알림 (새 등록 or 실패 있을 때만)

환경변수 (GH Secrets):
  DATABASE_URL              — Supabase pg URL
  KSE_URKEY / KSE_PASSWORD  — KSE OMS 자격증명
  QOO10_API_KEY / QOO10_USER_ID / QOO10_PASSWORD — QSM 자격증명
  SLACK_WEBHOOK_URL         — 실패/변화 알림 (없으면 stdout 만)
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import traceback
from datetime import date, timedelta
from typing import Any

# 프로젝트 루트를 path 에 추가 (스크립트 단독 실행 시)
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from qoo10 import api_client as qapi
from qoo10 import generator as qgen
from qoo10 import kse_client as ksec


KST = _dt.timezone(_dt.timedelta(hours=9))


def _now_kst_str() -> str:
    return _dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")


def _kse_collect(days_back: int = 1, days_fwd: int = 1) -> dict[str, str]:
    """KSE OMS 로그인 → 검색 → {packNo: waybill}."""
    today = _dt.datetime.now(KST).date()
    return ksec.fetch_waybills(today - timedelta(days=days_back),
                               today + timedelta(days=days_fwd))


def _qsm_current_stat(sak: str, order_no: str) -> str | None:
    """QSM 에서 해당 orderNo 의 현재 ShippingStat 조회. 없으면 None."""
    today = _dt.datetime.now(KST).date()
    sd = (today - timedelta(days=45)).strftime('%Y%m%d')
    ed = (today + timedelta(days=1)).strftime('%Y%m%d')
    stat, _item = qapi.fetch_order_by_no(sak, order_no, sd, ed)
    return stat


def run() -> dict[str, Any]:
    started_at = _now_kst_str()
    result: dict[str, Any] = {
        "started_at": started_at,
        "kse_map_size": 0,
        "briefs_scanned": 0,
        "attempted": 0,
        "registered": 0,
        "already_shipped_skipped": 0,
        "failed": 0,
        "brief_marks": [],  # consumed 마크한 brief id 목록
        "errors": [],       # 개별 등록 실패 상세
        "top_error": None,  # KSE 로그인 실패 등 fatal
    }

    # 1) KSE 매핑
    try:
        kse_map = _kse_collect()
    except Exception as ex:
        result["top_error"] = f"KSE 수집 실패: {ex}"
        return result
    result["kse_map_size"] = len(kse_map)
    if not kse_map:
        return result

    # 2) pending brief 조회
    try:
        pending = qgen.list_pending_briefs(include_consumed=False, limit=100)
    except Exception as ex:
        result["top_error"] = f"pending brief 조회 실패: {ex}"
        return result
    result["briefs_scanned"] = len(pending)
    if not pending:
        return result

    # 3) QSM SAK
    try:
        sak = qapi.get_sak()
    except Exception as ex:
        result["top_error"] = f"QSM SAK 발급 실패: {ex}"
        return result

    SHIPPED = qapi.SHIPPING_STAT_DELIVERY  # "4" = 배송중 (이미 등록된 상태)

    # 4) 각 brief 처리
    for pb in pending:
        try:
            content, _fname = qgen.load_pending_brief(pb["id"])
            rows = qgen.parse_qsm_csv(content)
        except Exception as ex:
            result["errors"].append({"brief_id": pb["id"], "phase": "parse", "detail": str(ex)})
            continue

        brief_total = 0
        brief_success = 0
        brief_skipped = 0

        for r in rows:
            cart = str(r.get("장바구니번호", "") or "").strip()
            order = str(r.get("주문번호", "") or "").strip()
            if not cart or not order or cart not in kse_map:
                continue
            waybill = kse_map[cart]
            brief_total += 1

            # 4a) QSM 상태 사전 조회 — 이미 배송중이면 skip
            try:
                cur_stat = _qsm_current_stat(sak, order)
            except Exception:
                cur_stat = None
            if cur_stat == SHIPPED:
                brief_skipped += 1
                result["already_shipped_skipped"] += 1
                continue

            # 4b) SetSendingInfo 호출
            result["attempted"] += 1
            try:
                reg = qapi.register_waybill(sak, order_no=order, tracking_no=waybill)
            except Exception as ex:
                result["failed"] += 1
                result["errors"].append({
                    "brief_id": pb["id"], "order_no": order, "waybill": waybill,
                    "phase": "SetSendingInfo", "detail": str(ex),
                })
                continue

            if reg.get("ok"):
                result["registered"] += 1
                brief_success += 1
            else:
                result["failed"] += 1
                result["errors"].append({
                    "brief_id": pb["id"], "order_no": order, "waybill": waybill,
                    "phase": "SetSendingInfo", "code": reg.get("code"), "msg": reg.get("msg"),
                })

        # 4c) brief 전체가 처리됐으면 consumed 마크
        # 판단: rows 중 KSE 매핑 있는 모든 orderNo 가 성공 or already-shipped 로 처리됨
        if brief_total > 0 and (brief_success + brief_skipped) == brief_total:
            try:
                qgen.mark_brief_consumed(pb["id"])
                result["brief_marks"].append(pb["id"])
            except Exception as ex:
                result["errors"].append({
                    "brief_id": pb["id"], "phase": "mark_consumed", "detail": str(ex),
                })

    result["finished_at"] = _now_kst_str()
    return result


def _notify_slack(webhook: str, result: dict[str, Any]) -> None:
    """알림 조건: (a) 새 등록 발생 or (b) 실패 or (c) top_error. 아니면 조용."""
    has_change = (result["registered"] > 0 or result["failed"] > 0 or result.get("top_error"))
    if not has_change:
        return

    if result.get("top_error"):
        color = "danger"
        title = "🚨 KSE→QSM 자동 송장 등록 — 치명적 실패"
        body = f"*원인*: {result['top_error']}"
    elif result["failed"] > 0:
        color = "warning"
        title = f"⚠️ KSE→QSM 자동 송장 등록 — 부분 실패 ({result['registered']} 성공 / {result['failed']} 실패)"
        errs = "\n".join(
            f"• brief#{e.get('brief_id')} orderNo={e.get('order_no')}: "
            f"{e.get('msg') or e.get('detail') or e.get('code')}"
            for e in result["errors"][:10]
        )
        body = f"*실행 시각*: {result.get('started_at')}\n*실패 상세*:\n{errs}"
    else:
        color = "good"
        title = f"✅ KSE→QSM 자동 송장 등록 — {result['registered']}건 등록"
        body = (f"*실행 시각*: {result.get('started_at')}\n"
                f"KSE 매핑: {result['kse_map_size']}건 · brief {result['briefs_scanned']}개 스캔 · "
                f"중복 skip {result['already_shipped_skipped']}건 · "
                f"consumed 마크 {len(result['brief_marks'])}개")

    payload = {
        "attachments": [{
            "color": color,
            "title": title,
            "text": body,
            "footer": "kat-outbound-hub · scripts/kse_auto_sync.py",
        }]
    }
    try:
        import requests
        requests.post(webhook, json=payload, timeout=10)
    except Exception as ex:
        print(f"[slack notify failed] {ex}", file=sys.stderr)


def main() -> int:
    try:
        result = run()
    except Exception as ex:
        traceback.print_exc()
        result = {
            "started_at": _now_kst_str(), "top_error": f"unhandled: {ex}",
            "registered": 0, "failed": 0, "errors": [],
        }

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if webhook:
        _notify_slack(webhook, result)

    if result.get("top_error"):
        return 1
    if result.get("failed", 0) > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
