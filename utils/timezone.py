"""
한국(서울) 시간대 헬퍼.

Streamlit Cloud 는 UTC 로 동작 — datetime.date.today() 가 KST 자정~오전 9시 사이엔
어제 날짜를 반환. 사용자 작업일 default 가 잘못 채워지는 문제 회피용.
"""
import datetime

try:
    from zoneinfo import ZoneInfo
    _KST = ZoneInfo('Asia/Seoul')
except Exception:
    _KST = datetime.timezone(datetime.timedelta(hours=9))


def kst_now() -> datetime.datetime:
    return datetime.datetime.now(_KST)


def kst_today() -> datetime.date:
    return kst_now().date()
