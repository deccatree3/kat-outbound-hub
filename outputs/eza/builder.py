"""
이지어드민(EZA) 발주서 / 송장 빌더.

채널별 raw → EZA 업로드 양식. EZA 안에서 다른 채널과 통합되어 다원으로.

현재 지원:
  - 메이커스 (cachers_makers) → 메이커스 EZA 발주서 (8컬럼, .xls)
  - 국내몰 송장 — 다원 채번 → EZA 송장 업로드 양식 (.xlsx)
"""
import datetime
import io
from typing import Dict, List

import openpyxl
import xlrd
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


# ─── 국내몰 송장 양식 (이지어드민 업로드용) ──────────────────────────────

EZA_WAYBILL_DEFAULT_CARRIER = 'CJ대한통운'


def build_eza_waybill_xlsx(daone_invoice_xls_bytes: bytes,
                           carrier: str = EZA_WAYBILL_DEFAULT_CARRIER) -> tuple[bytes, Dict]:
    """다원 채번.xls (12컬럼) → 이지어드민 송장 업로드 양식.xlsx (9컬럼).

    출력 컬럼 위치 (B/C/F~I 빈 컬럼 그대로 유지 — 양식 호환):
      A 택배사 / D 송장번호 / E 관리번호

    매핑:
      A 택배사   = carrier (default 'CJ대한통운')
      D 송장번호 = 채번 파일의 운송장번호 (하이픈 제거)
      E 관리번호 = 채번 파일의 주문번호

    반환: (xlsx_bytes, info)
      info = {'filled': N, 'skipped': [{ 행, 원인 }]}
    """
    wb_in = xlrd.open_workbook(file_contents=daone_invoice_xls_bytes)
    ws_in = wb_in.sheet_by_index(0)
    if ws_in.nrows < 2:
        raise RuntimeError("채번 파일에 데이터가 없습니다.")
    headers = [str(ws_in.cell_value(0, c)).strip() for c in range(ws_in.ncols)]

    def find_idx(*names):
        for n in names:
            if n in headers:
                return headers.index(n)
        return None

    order_i = find_idx('주문번호')
    waybill_i = find_idx('운송장번호', '송장번호')
    if order_i is None or waybill_i is None:
        raise RuntimeError(
            f"채번 파일에서 '주문번호' 또는 '운송장번호' 컬럼을 찾지 못했습니다. "
            f"실제 헤더: {headers}"
        )

    wb_out = openpyxl.Workbook()
    ws_out = wb_out.active
    ws_out.title = 'Sheet1'
    ws_out.cell(1, 1, '택배사')
    ws_out.cell(1, 4, '송장번호')
    ws_out.cell(1, 5, '관리번호')
    # 컬럼 폭
    for letter, w in [('A', 13), ('B', 13), ('C', 13), ('D', 15), ('E', 13)]:
        ws_out.column_dimensions[letter].width = w

    filled = 0
    skipped: List[Dict] = []
    for r in range(1, ws_in.nrows):
        order_raw = ws_in.cell_value(r, order_i)
        waybill_raw = ws_in.cell_value(r, waybill_i)

        # 주문번호 정수형 정규화 (xlrd가 float로 읽을 수 있음)
        try:
            order_no = str(int(float(order_raw))) if order_raw not in ('', None) else ''
        except (ValueError, TypeError):
            order_no = str(order_raw).strip()

        waybill = ''.join(c for c in str(waybill_raw) if c.isdigit())

        if not order_no or not waybill:
            skipped.append({'행': r + 1, '주문번호': order_no, '운송장번호': str(waybill_raw),
                            '원인': '필수값 없음'})
            continue

        out_r = filled + 2  # 헤더 1행 다음부터
        ws_out.cell(out_r, 1, carrier)
        ws_out.cell(out_r, 4, waybill)
        ws_out.cell(out_r, 5, order_no)
        filled += 1

    buf = io.BytesIO()
    wb_out.save(buf)
    return buf.getvalue(), {'filled': filled, 'skipped': skipped}


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
