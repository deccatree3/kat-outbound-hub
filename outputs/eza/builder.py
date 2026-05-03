"""
이지어드민(EZA) 발주서 빌더.

채널별 raw 주문서 → EZA 업로드 양식(.xls). EZA 안에서 다른 캐처스 채널과
통합되어 통합 다원 발주서로 출력됨 (= 다원 입장 단순화 흐름).

현재 지원:
  - 메이커스 (cachers_makers) → 메이커스 EZA 발주서 (8컬럼, .xls)
"""
import datetime
import io
from typing import Dict, List

import xlwt


# ─── 메이커스 EZA 발주서 (8컬럼) ─────────────────────────────────────

MAKERS_EZA_HEADERS = [
    '주문번호', '상품명', '수량', '주문일',
    '수령인', '수령자연락처', '주소', '배송메모',
]


def _to_excel_date(value) -> tuple:
    """주문일시 → (날짜셀 값, 적용 스타일).
    datetime/문자열 → datetime.date 객체. 실패 시 (원본 문자열, None).
    """
    if isinstance(value, datetime.datetime):
        return value.date(), 'date'
    if isinstance(value, datetime.date):
        return value, 'date'
    if isinstance(value, str) and value:
        try:
            return datetime.datetime.strptime(value[:19], '%Y-%m-%d %H:%M:%S').date(), 'date'
        except ValueError:
            try:
                return datetime.datetime.strptime(value[:10], '%Y-%m-%d').date(), 'date'
            except ValueError:
                return value, None
    return ('' if value is None else str(value)), None


def build_makers_eza_xls(makers_rows: List[Dict]) -> bytes:
    """메이커스 주문내역 dict → EZA 발주서 .xls bytes.

    매핑 미사용 — 상품명 + 옵션 그대로 한 컬럼으로 결합. EZA가 자체 매핑 보유.
    """
    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Sheet1')

    date_style = xlwt.easyxf(num_format_str='YYYY-MM-DD')

    for ci, h in enumerate(MAKERS_EZA_HEADERS):
        ws.write(0, ci, h)

    for ri, r in enumerate(makers_rows, 1):
        order_no = r.get('주문번호', '')
        # 주문번호: 숫자형이면 정수로
        try:
            order_no_v = int(order_no) if str(order_no).strip() else ''
        except (ValueError, TypeError):
            order_no_v = str(order_no)

        product = (r.get('상품') or '').strip()
        option = (r.get('옵션') or '').strip()
        product_full = f"{product}_{option}" if option else product

        try:
            qty = int(float(r.get('수량', 1) or 1))
        except (ValueError, TypeError):
            qty = 1

        date_val, date_kind = _to_excel_date(r.get('주문일시'))

        recipient = (r.get('수령인명') or '').strip()
        phone = str(r.get('수령인 연락처1', '')).strip()
        address = (r.get('배송주소') or '').strip()
        memo = (r.get('배송메시지') or '').strip()

        ws.write(ri, 0, order_no_v)
        ws.write(ri, 1, product_full)
        ws.write(ri, 2, qty)
        if date_kind == 'date':
            ws.write(ri, 3, date_val, date_style)
        else:
            ws.write(ri, 3, date_val)
        ws.write(ri, 4, recipient)
        ws.write(ri, 5, phone)
        ws.write(ri, 6, address)
        ws.write(ri, 7, memo)

    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()
