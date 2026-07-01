"""KSE JP OMS 자동 수집 클라이언트.

기존 흐름: 사용자가 KSE OMS 에서 xlsx 다운로드 → 대시보드 업로드.
자동 흐름 (이 모듈): 로그인 → 검색 API → {주문번호: 송장번호} dict 직접 획득.

캡처된 스펙 (2026-07-01, 실사이트 XHR 분석):
    Base URL     : https://jp.ksewms.com
    로그인       : POST /backed/system/login  body {urkey, password}  → JWT
    검색         : POST /omsbackend/orderController/selectOrderHd
                   header Authorization: <JWT>  (Bearer prefix 없음)
    응답 경로    : data.LIST.rtnGrid[] each item →
                   externorderkey (주문번호), waybillno (송장번호),
                   or_cancel_yn ("N" 아니면 취소), ifstatus ("90"=전송완료),
                   dlcompany (택배사 코드)

자격증명 관리 (qoo10_credentials 와 동일 패턴):
    우선순위: 환경변수 > Streamlit secrets > DB > (config.json 없음)
    DB 테이블: kse_credentials (id=1 single row upsert)
    사이드바 UI: channels/qoo10_japan/page.py 의 render_credentials_sidebar_kse()
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Optional

import requests

LOG = logging.getLogger(__name__)

BASE_URL = "https://jp.ksewms.com"
# 실제 브라우저 XHR 캡처 (2026-07-01):
# - login  : POST /amsbackend/backed/system/login   body {user_id, password, code}
# - search : POST /omsbackend/orderController/selectOrderHd   header Authorization: <JWT>
# - JWT    : 응답 data.token (796 chars, exp 24h)
LOGIN_URL = f"{BASE_URL}/amsbackend/backed/system/login"
SEARCH_URL = f"{BASE_URL}/omsbackend/orderController/selectOrderHd"

DEFAULT_TIMEOUT = 30.0
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# 인터페이스 상태값 (실사이트 확인)
IFSTATUS_SENT = "90"   # 전송(생성) 완료 — 송장 발급 완료 상태

JST = timezone(timedelta(hours=9))


class KseClientError(RuntimeError):
    pass


@dataclass
class KseAuth:
    urkey: str
    password: str
    ctkey: str = "KE00003"     # 캐처스 화주 (JWT payload 에서 확인)
    loggrpcd: str = "1"        # 물류그룹 코드


# ========================================================================== #
# DB 헬퍼 (qoo10_credentials 와 동일 패턴)
# ========================================================================== #

_DB_AVAILABLE = None


def _try_import_pg():
    global _DB_AVAILABLE
    if _DB_AVAILABLE is not None:
        return _DB_AVAILABLE
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        _base = os.path.dirname(_here)
        if os.path.join(_base, 'db') not in sys.path:
            sys.path.insert(0, os.path.join(_base, 'db'))
        import pg as _pg  # type: ignore
        _DB_AVAILABLE = _pg
    except Exception:
        _DB_AVAILABLE = False
    return _DB_AVAILABLE


CREDS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS kse_credentials (
    id INTEGER PRIMARY KEY,
    urkey TEXT,
    password TEXT,
    ctkey TEXT,
    loggrpcd TEXT,
    updated_at TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')
);
"""


def _ensure_creds_table() -> bool:
    pg = _try_import_pg()
    if not pg:
        return False
    try:
        conn = pg.connect()
        with conn.cursor() as cur:
            cur.execute(CREDS_TABLE_DDL)
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def load_credentials_from_db() -> Dict:
    """DB에서 단일 자격증명 행 로드. 없으면 빈 dict."""
    pg = _try_import_pg()
    if not pg:
        return {}
    try:
        _ensure_creds_table()
        conn = pg.connect(autocommit=True)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT urkey, password, ctkey, loggrpcd, updated_at
                FROM kse_credentials WHERE id = 1
            """)
            row = cur.fetchone()
        conn.close()
        if not row:
            return {}
        return {
            'urkey': row[0], 'password': row[1],
            'ctkey': row[2], 'loggrpcd': row[3],
            'updated_at': row[4],
        }
    except Exception:
        return {}


def save_credentials_to_db(
    urkey: Optional[str] = None,
    password: Optional[str] = None,
    ctkey: Optional[str] = None,
    loggrpcd: Optional[str] = None,
) -> bool:
    """DB 자격증명 upsert. 빈 값(None/'')은 기존 값 유지."""
    pg = _try_import_pg()
    if not pg:
        return False
    _ensure_creds_table()
    existing = load_credentials_from_db()
    new_ur = urkey if urkey else existing.get('urkey')
    new_pw = password if password else existing.get('password')
    new_ct = ctkey if ctkey else (existing.get('ctkey') or 'KE00003')
    new_lg = loggrpcd if loggrpcd else (existing.get('loggrpcd') or '1')
    try:
        conn = pg.connect()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO kse_credentials (id, urkey, password, ctkey, loggrpcd, updated_at)
                VALUES (1, %s, %s, %s, %s, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul'))
                ON CONFLICT (id) DO UPDATE SET
                    urkey = EXCLUDED.urkey,
                    password = EXCLUDED.password,
                    ctkey = EXCLUDED.ctkey,
                    loggrpcd = EXCLUDED.loggrpcd,
                    updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')
            """, (new_ur, new_pw, new_ct, new_lg))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def load_credentials() -> Dict[str, str]:
    """자격증명 로드 우선순위: 환경변수 > Streamlit secrets > DB.

    - 배포/CI: env (KSE_URKEY / KSE_PASSWORD)
    - Streamlit Cloud: st.secrets["kse_jp"]
    - 사용자 입력: DB (사이드바 UI 저장)
    """
    creds: Dict[str, Optional[str]] = {
        'urkey': None, 'password': None, 'ctkey': None, 'loggrpcd': None,
    }

    # 3) DB (사이드바 UI 저장)
    db = load_credentials_from_db()
    for k in ('urkey', 'password', 'ctkey', 'loggrpcd'):
        if db.get(k):
            creds[k] = db[k]

    # 2) Streamlit secrets
    try:
        import streamlit as _st  # type: ignore
        try:
            sec = _st.secrets.get('kse_jp', {}) if hasattr(_st, 'secrets') else {}
            for k in ('urkey', 'password', 'ctkey', 'loggrpcd'):
                v = sec.get(k) if hasattr(sec, 'get') else sec[k] if k in sec else None
                if v:
                    creds[k] = v
        except (FileNotFoundError, Exception):
            pass
    except ImportError:
        pass

    # 1) 환경변수 (최우선)
    if os.environ.get('KSE_URKEY'):
        creds['urkey'] = os.environ['KSE_URKEY']
    if os.environ.get('KSE_PASSWORD'):
        creds['password'] = os.environ['KSE_PASSWORD']
    if os.environ.get('KSE_CTKEY'):
        creds['ctkey'] = os.environ['KSE_CTKEY']
    if os.environ.get('KSE_LOGGRPCD'):
        creds['loggrpcd'] = os.environ['KSE_LOGGRPCD']

    # 기본값
    if not creds.get('ctkey'):
        creds['ctkey'] = 'KE00003'
    if not creds.get('loggrpcd'):
        creds['loggrpcd'] = '1'
    return creds  # type: ignore[return-value]


def get_credentials_status() -> Dict:
    """UI 표시용 상태 dict.
    반환: {'configured': bool, 'updated_at': datetime|None, 'source': str}
    """
    creds = load_credentials()
    configured = bool(creds.get('urkey') and creds.get('password'))
    db = load_credentials_from_db()
    # source 판정
    if os.environ.get('KSE_URKEY') or os.environ.get('KSE_PASSWORD'):
        source = 'env'
    else:
        try:
            import streamlit as _st  # type: ignore
            sec = _st.secrets.get('kse_jp', {}) if hasattr(_st, 'secrets') else {}
            if sec.get('urkey') or sec.get('password'):
                source = 'secrets'
            elif db.get('urkey'):
                source = 'db'
            else:
                source = 'none'
        except Exception:
            source = 'db' if db.get('urkey') else 'none'
    return {
        'configured': configured,
        'updated_at': db.get('updated_at'),
        'source': source,
        'urkey': creds.get('urkey'),
    }


# ========================================================================== #
# 로그인 + 검색
# ========================================================================== #

def _extract_jwt(resp: requests.Response) -> str:
    """로그인 응답에서 JWT 추출.
    1) 응답 헤더의 다양한 후보 (Authorization / Access-Token / X-* 등)
    2) Set-Cookie 안에 JWT 형태 (eyJ...) 값
    3) 응답 body JSON (data.token / token / jwt 등 다양한 경로)
    실패 시 응답 구조 요약을 에러에 포함.
    """
    header_candidates = [
        "Authorization", "authorization",
        "Access-Token", "access-token", "AccessToken",
        "X-Access-Token", "x-access-token",
        "X-Auth-Token", "x-auth-token",
        "X-Authorization", "x-authorization",
        "Token", "token",
    ]
    for h in header_candidates:
        v = resp.headers.get(h)
        if not v:
            continue
        tok = v.split(" ", 1)[1] if " " in v else v
        if tok.startswith("eyJ"):
            return tok

    # 2) Set-Cookie 검색
    set_cookies_raw = resp.headers.get("Set-Cookie", "")
    if set_cookies_raw:
        for chunk in set_cookies_raw.split(","):
            for part in chunk.split(";"):
                kv = part.strip()
                if "=" in kv:
                    _, val = kv.split("=", 1)
                    val = val.strip()
                    if val.startswith("eyJ"):
                        return val
    # requests 는 이미 세션에 쿠키를 저장 — 세션의 cookiejar 에서도 조회
    for cookie in resp.cookies:
        if isinstance(cookie.value, str) and cookie.value.startswith("eyJ"):
            return cookie.value

    # 3) body JSON
    body_text = resp.text or ""
    if body_text.strip():
        try:
            j = resp.json()
        except Exception:
            j = None
        if isinstance(j, dict):
            for path in [
                ("data", "token"), ("data", "accessToken"), ("data", "jwt"),
                ("data", "authorization"), ("data", "Authorization"),
                ("token",), ("accessToken",), ("jwt",), ("authorization",), ("Authorization",),
            ]:
                cur = j
                ok = True
                for k in path:
                    if isinstance(cur, dict) and k in cur:
                        cur = cur[k]
                    else:
                        ok = False
                        break
                if ok and isinstance(cur, str) and cur.startswith("eyJ"):
                    return cur

    # 실패 — 응답 구조 요약을 에러에 포함
    hdr_keys = sorted(resp.headers.keys())
    raise KseClientError(
        f"로그인 응답에 JWT 없음. "
        f"status={resp.status_code}, "
        f"location={resp.headers.get('Location')}, "
        f"content_type={resp.headers.get('Content-Type')}, "
        f"header_keys={hdr_keys}, "
        f"cookies={[c.name for c in resp.cookies]}, "
        f"body_head={body_text[:300] or '(empty)'}"
    )


def _login(session: requests.Session, auth: KseAuth) -> str:
    # 브라우저 XHR 캡처 재현: body 필드는 user_id/password/code (captcha 없으면 code="").
    resp = session.post(
        LOGIN_URL,
        json={"user_id": auth.urkey, "password": auth.password, "code": ""},
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/oms/login",
        },
        timeout=DEFAULT_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise KseClientError(f"로그인 실패 status={resp.status_code} body={resp.text[:300]}")
    return _extract_jwt(resp)


def _decode_jwt_payload(jwt: str) -> dict:
    """JWT 의 payload 부분 (segment 1) 을 base64 decode → dict. 검증 없이 파싱만."""
    try:
        seg = jwt.split(".")[1]
        # JWT base64url: padding 없음 → 4의 배수 맞추기
        seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return {}


def _build_search_body(auth: KseAuth, start_dt: datetime, end_dt: datetime, jwt: str) -> dict:
    """selectOrderHd body 구성. 브라우저 XHR 재현.

    Note: 서버는 body 에 세션 관련 필드(sessionUserId, COMMON, USER_INFO, MAP, LIST)
    가 함께 있어야 200 반환. USER_INFO 는 JWT payload 를 그대로 심으면 됨.
    """
    def _fmt(dt: datetime) -> str:
        return dt.strftime("%Y%m%d%H%M%S")

    def _iso_utc(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    user_info = _decode_jwt_payload(jwt)

    return {
        "PARAM": {
            "param": {"ctkey": auth.ctkey, "searchFlag": ""},
            "currentPage": 1,
            "pagingLimit": 100000,
            "sortingFields": [],
            "ctkey": auth.ctkey,
            "ctKey": 0,
            "owkey": auth.urkey,
            "owkeym": auth.urkey,
        },
        "SEARCHLIST": {
            "0": {"operator": "=", "value": auth.urkey, "ussc_label": "OWKEY",
                  "gtmapply_chk": "Y", "dbColoum": "OWKEY",
                  "required": True, "isChecked": True, "sctype": "EDITBOXSEARCHPOPUP"},
            "1": {"operator": "BETWEEN",
                  "value": [_iso_utc(start_dt), _iso_utc(end_dt)],
                  "ussc_label": "OR_HDDATE", "gtmapply_chk": "Y", "dbColoum": "OR_HDDATE",
                  "required": True, "isChecked": True, "sctype": "BETWEENDATEFIELD",
                  "fromVal": _fmt(start_dt), "toVal": _fmt(end_dt)},
            "2": {"operator": "=", "value": auth.loggrpcd, "ussc_label": "LOGGRPCD",
                  "gtmapply_chk": "Y", "dbColoum": "LOGGRPCD",
                  "required": False, "isChecked": True, "sctype": "EDITBOXSEARCHPOPUP"},
            "3": {"operator": "=", "value": "N", "ussc_label": "OR_CANCEL_YN",
                  "gtmapply_chk": "Y", "dbColoum": "OR_CANCEL_YN",
                  "required": False, "isChecked": True, "sctype": "COMBOBOX"},
            "4": {"operator": "=", "value": IFSTATUS_SENT, "ussc_label": "IFSTATUS",
                  "gtmapply_chk": "Y", "dbColoum": "IFSTATUS",
                  "required": False, "isChecked": True, "sctype": "COMBOBOX"},
        },
        # 세션 필드 (axios interceptor 재현)
        "sessionUserId": auth.urkey,
        "sessionUserCtkey": auth.ctkey,
        "lakey": "KOR",
        "activeApp": "ICOM",
        "ctkey": auth.ctkey,
        "COMMON": {
            "beanId": "orderController",
            "usKey": "US00000220",
            "LAKEY": "KOR",
            "DEVICE": "PC",
            "ACTIVE_APP": "ICOM",
            "apKey": "ICOM",
            "callMethod": "selectOrderHd",
            "TIMEZONE": 9,
            "eqtype": 10,
            "serverIp": "/omsbackend",
            "ctKey": auth.ctkey,
            "urKey": auth.urkey,
            "sessionUserId": "",
            "OWKEY_AUTH": auth.urkey,
            "CTKEY_AUTH": auth.ctkey,
            "APKEY_AUTH": "ADMIN,ICOM",
        },
        "USER_INFO": user_info,
        "MAP": {},
        "LIST": {},
    }


def _parse_list(data_list: dict) -> list[dict]:
    """응답 data.LIST 파싱. 캡처 시점 구조: {"rtnGrid": [row, row, ...]}."""
    if not isinstance(data_list, dict):
        return []
    grid = data_list.get("rtnGrid")
    if isinstance(grid, list):
        return grid
    rows = []
    for k in sorted(data_list.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
        v = data_list[k]
        if isinstance(v, list):
            rows.extend(v)
        elif isinstance(v, dict):
            rows.append(v)
    return rows


def fetch_waybills(
    start_date: date,
    end_date: date,
    urkey: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, str]:
    """KSE OMS 로그인 → 검색 → {주문번호: 송장번호} 반환.

    Args:
        start_date, end_date: 출고예정일 필터 (JST 기준, 종일 포함)
        urkey, password: 명시적 자격증명 (없으면 env/secrets/DB 순으로 로드)

    Returns:
        {externorderkey: waybillno} — 취소건·송장미발급건 자동 제외

    Raises:
        KseClientError: 자격증명 없음, 로그인 실패, 검색 실패 등
    """
    if urkey and password:
        auth = KseAuth(urkey=urkey, password=password)
    else:
        c = load_credentials()
        if not (c.get('urkey') and c.get('password')):
            raise KseClientError(
                "KSE OMS 자격증명이 없습니다. 사이드바의 'KSE OMS 자격증명' expander "
                "또는 secrets.toml `[kse_jp]` 또는 환경변수 KSE_URKEY/KSE_PASSWORD 로 등록하세요."
            )
        auth = KseAuth(
            urkey=c['urkey'], password=c['password'],
            ctkey=c.get('ctkey') or 'KE00003',
            loggrpcd=c.get('loggrpcd') or '1',
        )

    start_dt = datetime.combine(start_date, time(0, 0, 0), tzinfo=JST)
    end_dt = datetime.combine(end_date, time(23, 59, 59), tzinfo=JST)

    with requests.Session() as sess:
        sess.headers["User-Agent"] = DEFAULT_UA
        token = _login(sess, auth)
        LOG.info("KSE OMS 로그인 성공 (JWT %d chars)", len(token))

        body = _build_search_body(auth, start_dt, end_dt, token)
        resp = sess.post(
            SEARCH_URL,
            json=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": token,
                "Accept": "application/json, text/plain, */*",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code >= 400:
            raise KseClientError(f"검색 실패 status={resp.status_code} body={resp.text[:300]}")
        j = resp.json()
        if j.get("code") != 200:
            raise KseClientError(f"검색 응답 오류: {j.get('message')} (code={j.get('code')})")

        rows = _parse_list(j.get("data", {}).get("LIST", {}))
        mapping: Dict[str, str] = {}
        skipped_cancelled = 0
        skipped_no_waybill = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            order_no = row.get("externorderkey")
            waybill = row.get("waybillno")
            cancelled = str(row.get("or_cancel_yn") or "").strip().upper()
            if cancelled == "Y":
                skipped_cancelled += 1
                continue
            if not (order_no and waybill):
                skipped_no_waybill += 1
                continue
            mapping[str(order_no).strip()] = str(waybill).strip()

        LOG.info(
            "KSE 자동 수집: %d 건 매핑 (총 %d rows, 취소 %d, 송장미발급 %d)",
            len(mapping), len(rows), skipped_cancelled, skipped_no_waybill,
        )
        return mapping


def test_login() -> Dict:
    """저장된 자격증명으로 로그인만 시도. UI '연결 테스트' 버튼용.
    반환: {'ok': bool, 'message': str, 'jwt_len': int|None}
    """
    c = load_credentials()
    if not (c.get('urkey') and c.get('password')):
        return {'ok': False, 'message': '자격증명이 등록되어 있지 않습니다.', 'jwt_len': None}
    try:
        with requests.Session() as sess:
            sess.headers["User-Agent"] = DEFAULT_UA
            auth = KseAuth(urkey=c['urkey'], password=c['password'])
            token = _login(sess, auth)
        return {'ok': True, 'message': '로그인 성공', 'jwt_len': len(token)}
    except KseClientError as ex:
        return {'ok': False, 'message': str(ex), 'jwt_len': None}
    except Exception as ex:
        return {'ok': False, 'message': f'{type(ex).__name__}: {ex}', 'jwt_len': None}
