"""
다원 발주서 빌더 — EZA 확장주문검색.xls(신양식, 22컬럼) → 다원 표준 발주서.xlsx.

신양식 EZA는 다원 발주서와 거의 동일한 컬럼 구성을 가짐. 변환 규칙:

  EZA 컬럼              →  다원 컬럼
  --------------------     -----------------
  판매처그룹              →  (drop, 제품코드 분기 조건만 사용)
  몰명(또는 몰코드)        →  몰명(또는 몰코드)  (빈값 → "000000000001")
  출하의뢰번호            →  출하의뢰번호
  출하의뢰항번            →  출하의뢰항번
  주문번호               →  고객주문번호
  상품명                 →  상품명
  제품코드               →  제품코드  (빈값 → 판매처그룹="캐처스"이면 상품메모, 그 외는 바코드)
  바코드                 →  (제품코드 fallback)
  상품메모               →  (캐처스 제품코드 fallback)
  상품수량               →  주문수량
  주문자이름             →  주문자명
  주문자연락처1           →  주문자연락처1
  주문자연락처2           →  주문자연락처2
  수취인명               →  수취인명
  수취인연락처1           →  수취인연락처1
  수취인연락처2           →  수취인연락처2
  수취인우편번호          →  수취인우편번호
  수취인주소1             →  수취인주소1
  주소2                  →  주소2  (빈값 → 수취인주소1 복사)
  배송메시지              →  배송메시지
  송장번호                →  송장번호
  택배사명                →  택배사명

출력은 단일 `발주서` 시트, 다원 19컬럼.
"""
import io
from typing import Dict, List

import openpyxl
from openpyxl.utils import get_column_letter
import xlrd


DAONE_HEADERS = [
    '몰명(또는 몰코드)',
    '출하의뢰번호',
    '출하의뢰항번',
    '고객주문번호',
    '상품명',
    '제품코드',
    '주문수량',
    '주문자명',
    '주문자연락처1',
    '주문자연락처2',
    '수취인명',
    '수취인연락처1',
    '수취인연락처2',
    '수취인우편번호',
    '수취인주소1',
    '주소2',
    '배송메시지',
    '송장번호',
    '택배사명',
]

# 다원 몰코드 기본값 (EZA가 비워서 보냈을 때 채움)
DEFAULT_몰코드 = '000000000001'

# 헤더 직접 매핑 (분기 규칙은 transform_to_daone에서 별도 처리)
EZA_TO_DAONE = {
    '몰명(또는 몰코드)':  '몰명(또는 몰코드)',
    '출하의뢰번호':       '출하의뢰번호',
    '출하의뢰항번':       '출하의뢰항번',
    '주문번호':           '고객주문번호',
    '상품명':             '상품명',
    '제품코드':           '제품코드',
    '상품수량':           '주문수량',
    '주문자이름':         '주문자명',
    '주문자연락처1':      '주문자연락처1',
    '주문자연락처2':      '주문자연락처2',
    '수취인명':           '수취인명',
    '수취인연락처1':      '수취인연락처1',
    '수취인연락처2':      '수취인연락처2',
    '수취인우편번호':     '수취인우편번호',
    '수취인주소1':        '수취인주소1',
    '주소2':              '주소2',
    '배송메시지':         '배송메시지',
    '송장번호':           '송장번호',
    '택배사명':           '택배사명',
}


def _cell_str(value, ctype) -> str:
    """xlrd 셀 값 → 문자열. NUMBER 정수형 float은 정수 표기.
    텍스트 컬럼(우편번호/전화 등)의 leading-zero는 EZA가 TEXT로 보내므로 보존됨.
    """
    if value is None or value == '':
        return ''
    if ctype == 2:  # NUMBER
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    return str(value)


def parse_eza_xls(data: bytes) -> List[Dict]:
    """EZA 확장주문검색.xls bytes → 헤더 기반 dict 리스트.
    첫 시트의 row 0=헤더, row 1+=데이터로 가정.
    """
    wb = xlrd.open_workbook(file_contents=data)
    ws = wb.sheet_by_index(0)
    if ws.nrows < 1:
        return []
    headers = [str(ws.cell_value(0, c)).strip() for c in range(ws.ncols)]
    rows = []
    for r in range(1, ws.nrows):
        d = {h: _cell_str(ws.cell_value(r, c), ws.cell_type(r, c))
             for c, h in enumerate(headers)}
        rows.append(d)
    return rows


def transform_to_daone(eza_rows: List[Dict]) -> List[Dict]:
    """신양식 EZA dict → 다원 dict. 빈값 fallback / 판매처그룹 분기 적용."""
    out = []
    for eza in eza_rows:
        daone = {h: '' for h in DAONE_HEADERS}
        for eza_h, daone_h in EZA_TO_DAONE.items():
            daone[daone_h] = eza.get(eza_h, '')

        # 1) 몰명(또는 몰코드) 빈값 → 기본값
        if not str(daone.get('몰명(또는 몰코드)') or '').strip():
            daone['몰명(또는 몰코드)'] = DEFAULT_몰코드

        # 2) 주소2 빈값 → 수취인주소1 복사
        if not str(daone.get('주소2') or '').strip():
            daone['주소2'] = daone.get('수취인주소1', '')

        # 3) 제품코드 빈값 → 판매처그룹 분기
        if not str(daone.get('제품코드') or '').strip():
            group = str(eza.get('판매처그룹') or '').strip()
            if group == '캐처스':
                daone['제품코드'] = eza.get('상품메모', '')
            else:
                daone['제품코드'] = eza.get('바코드', '')

        # 주문수량 정수 보정
        q = daone.get('주문수량')
        if q not in (None, '', 0):
            try:
                daone['주문수량'] = int(float(q))
            except (ValueError, TypeError):
                pass

        out.append(daone)
    return out


def build_daone_xlsx(daone_rows: List[Dict]) -> bytes:
    """다원 발주서.xlsx bytes 생성. 단일 `발주서` 시트, 19 컬럼."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '발주서'
    ws.append(DAONE_HEADERS)
    for r in daone_rows:
        ws.append([r.get(h, '') for h in DAONE_HEADERS])
    widths = [14, 18, 14, 14, 40, 14, 8, 12, 16, 16, 12, 16, 16, 12, 50, 50, 30, 16, 12]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def convert_eza_to_daone(eza_bytes: bytes) -> tuple[bytes, int]:
    """원샷 변환: EZA xls bytes → 다원 xlsx bytes. (xlsx_bytes, row_count) 반환."""
    eza_rows = parse_eza_xls(eza_bytes)
    daone_rows = transform_to_daone(eza_rows)
    return build_daone_xlsx(daone_rows), len(daone_rows)
