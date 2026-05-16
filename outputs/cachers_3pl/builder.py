"""
캐처스 3PL 출고요청서 빌더.

EZA 확장주문검색.xls (신양식 31컬럼) → 캐처스-3PL-참기름-자연앤미 출고요청서.xls (25컬럼).

필터: 업로드 파일의 '공급처' 컬럼이 정확히 TARGET_SUPPLIER 인 행만 추출.

출력 포맷 (자연앤미 호환):
  - 레거시 Excel (.xls, xlwt) — 자연앤미 업로드 시스템이 .xlsx 미수용 가능성
  - 시트명: 'Worksheet'
  - 빈 셀: '' (빈 문자열) — None 회피
"""
import io
from typing import Dict, List, Tuple

import xlwt


TARGET_SUPPLIER = '캐처스-3PL-참기름-자연앤미'

# 출력 '공급처' 컬럼은 EZA 원본값과 무관하게 항상 이 고정값 (자연앤미 요청)
SUPPLIER_OUTPUT = '캐처스 자사'

OUTPUT_HEADERS = [
    '공급처', '주문일', '주문시간', '발주일', '발주시간', '몰명',
    '출하의뢰번호', '출하의뢰항번', '주문번호',
    '판매처 상품명', '판매처 옵션', '자체상품코드', '주문수량',
    '주문자이름', '주문자전화', '주문자휴대폰',
    '수령자이름', '수령자전화', '수령자휴대폰',
    '수령자우편번호', '수령자주소',
    '배송메모', 'CS', '송장번호', '택배사',
]

# 출고요청서 컬럼 → EZA 컬럼 매핑.
# 키 = 출고요청서 컬럼 (왼→오 순). 값 = EZA 컬럼명. None 이면 빈값.
OUTPUT_TO_EZA: Dict[str, str] = {
    '공급처':         '공급처',
    '주문일':         '주문일',
    '주문시간':       '주문시간',
    '발주일':         '발주일',
    '발주시간':       '발주시간',
    '몰명':           None,         # EZA 에 없음 → 빈값
    '출하의뢰번호':    '출하의뢰번호',
    '출하의뢰항번':    '출하의뢰항번',
    '주문번호':       '고객주문번호',
    '판매처 상품명':   '판매처 상품명',
    '판매처 옵션':    '판매처 옵션',
    '자체상품코드':    '제품코드',
    '주문수량':       '주문수량',
    '주문자이름':     '주문자이름',
    '주문자전화':     '주문자연락처2',
    '주문자휴대폰':   '주문자연락처1',
    '수령자이름':     '수취인명',
    '수령자전화':     '수취인연락처2',
    '수령자휴대폰':   '수취인연락처1',
    '수령자우편번호': '수취인우편번호',
    '수령자주소':     '수취인주소1',
    '배송메모':       '배송메시지',
    'CS':             'CS',
    '송장번호':       '송장번호',
    '택배사':         '택배사명',
}


def filter_target_rows(eza_rows: List[Dict]) -> List[Dict]:
    """공급처 == TARGET_SUPPLIER 인 행만 반환."""
    return [r for r in eza_rows
            if str(r.get('공급처', '')).strip() == TARGET_SUPPLIER]


def build_cachers_3pl_xlsx(eza_rows: List[Dict]) -> Tuple[bytes, int]:
    """필터된 EZA dict 들을 25컬럼 출고요청서.xls (xlwt) 로 빌드.

    함수명은 호환 위해 _xlsx 유지하지만 실제 출력은 .xls (자연앤미 업로드 호환).
    반환: (xls_bytes, target_row_count).
    """
    target_rows = filter_target_rows(eza_rows)

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Worksheet')

    # 헤더
    for c, h in enumerate(OUTPUT_HEADERS):
        ws.write(0, c, h)

    # 데이터
    for ri, r in enumerate(target_rows, start=1):
        for ci, h in enumerate(OUTPUT_HEADERS):
            if h == '공급처':
                v = SUPPLIER_OUTPUT
            else:
                eza_key = OUTPUT_TO_EZA.get(h)
                if eza_key is None:
                    v = ''
                else:
                    v = r.get(eza_key, '')
                    if (not v) and eza_key == '고객주문번호':
                        v = r.get('주문번호', '')
            # 주문수량은 숫자 셀로 기록 (샘플 c13 = NUM 타입). 값 로직은 그대로.
            if h == '주문수량':
                s = str(v).strip() if v is not None else ''
                try:
                    num = int(float(s.replace(',', '')))
                    ws.write(ri, ci, num)
                    continue
                except (ValueError, TypeError):
                    pass  # 숫자 변환 불가 시 아래 text 경로로 폴백
            # 빈 셀은 명시적으로 '' 로 기록 (자연앤미 호환)
            ws.write(ri, ci, v if v is not None else '')

    # 컬럼 폭 (xlwt: width = 1/256 character widths)
    widths = [22, 12, 10, 12, 10, 10, 22, 18, 14,
              30, 30, 18, 8, 14, 16, 16, 14, 16, 16, 12, 50, 30, 8, 16, 12]
    for i, w in enumerate(widths):
        if i < len(OUTPUT_HEADERS):
            ws.col(i).width = int(w * 256)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), len(target_rows)
