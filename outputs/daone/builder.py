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
from openpyxl.styles import PatternFill
from openpyxl.utils import get_column_letter
import xlrd

# 다원 발주서 헤더 행 배경색 (#E8E8E8)
_HEADER_FILL = PatternFill(start_color='E8E8E8', end_color='E8E8E8', fill_type='solid')
# KSE 큐텐 국내 빌드 시 'NO' 추가 컬럼 헤더 색상 (#FFFF00)
_NO_COL_HEADER_FILL = PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')


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


def build_daone_xlsx(daone_rows: List[Dict],
                     add_packing_columns: bool = False) -> bytes:
    """다원 발주서.xlsx bytes 생성. 단일 `발주서` 시트, 19 컬럼.

    add_packing_columns=True 면 마지막에 4 컬럼 추가 (모두 헤더 #FFFF00):
      인박스NO / 인박스 / 아웃박스 / 아웃박스NO

    각 행이 '_group_key' 메타 키(예: (도착지송장번호, 장바구니번호))를 가지고 있어야
    인박스NO가 그룹별로 부여됨. 인박스/아웃박스는 outputs.packing.boxes 의
    split_to_inboxes / select_outbox_for 알고리즘 사용.
    """
    from collections import OrderedDict, defaultdict
    from outputs.packing.boxes import split_to_inboxes, select_outbox_for

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '발주서'
    extra = ['인박스NO', '인박스', '아웃박스', '아웃박스NO'] if add_packing_columns else []
    headers = list(DAONE_HEADERS) + extra
    ws.append(headers)
    # 표준 헤더 색상
    for c in range(1, len(DAONE_HEADERS) + 1):
        ws.cell(1, c).fill = _HEADER_FILL
    # 패킹 컬럼 헤더 색상 (#FFFF00)
    if add_packing_columns:
        for c in range(len(DAONE_HEADERS) + 1, len(headers) + 1):
            ws.cell(1, c).fill = _NO_COL_HEADER_FILL

    # ─── 패킹 컬럼 계산 ───
    packing_per_row: List[Dict] = [{} for _ in daone_rows]
    if add_packing_columns:
        # 1) (_group_key) 그룹화 + 인박스NO 부여
        groups = OrderedDict()
        for i, r in enumerate(daone_rows):
            key = r.get('_group_key') or (i,)
            groups.setdefault(key, []).append(i)

        # 같은 _group_key = 같은 인박스NO. 그룹 내 행은 인박스 종류·수량 동일.
        # 단순화: 그룹 총 수량 기반으로 단일 인박스 결정 (분할 시는 첫 박스 종류 + 표기에 알림)
        inbox_no_to_type = {}
        for inbox_no, (gk, idxs) in enumerate(groups.items(), 1):
            total_qty = sum(int(daone_rows[i].get('주문수량', 0) or 0) for i in idxs)
            split = split_to_inboxes(total_qty)  # [(type, n), ...]
            if len(split) == 1:
                inbox_label = split[0][0]
            else:
                # 11+ 분할 케이스 — 한 인박스NO에 여러 박스. 표기에 명시.
                inbox_label = ' + '.join(f"{t}×{n}" for t, n in split) + ' (분할)'
            inbox_no_to_type[inbox_no] = inbox_label
            for i in idxs:
                packing_per_row[i]['inbox_no'] = inbox_no
                packing_per_row[i]['inbox'] = inbox_label

        # 2) 같은 인박스 종류 별로 모아 아웃박스 결정 (Best-Fit, fit 한도 내 같은 아웃박스NO)
        inbox_nos_by_type = defaultdict(list)
        for ibox_no, label in inbox_no_to_type.items():
            inbox_nos_by_type[label].append(ibox_no)

        outbox_by_inbox = {}  # inbox_no → (outbox_name, outbox_no)
        outbox_no_counter = 0
        for inbox_label, ibox_no_list in inbox_nos_by_type.items():
            # 11+ 분할 라벨은 자체적으로 outbox 분할 처리 — 우선 인박스 종류는 라벨 첫 단어
            primary_inbox = inbox_label.split('×')[0].split(' + ')[0].strip()
            outbox_name, fit_limit = select_outbox_for(primary_inbox, len(ibox_no_list))
            if not outbox_name:
                outbox_name, fit_limit = ('미정', max(1, len(ibox_no_list)))
            for chunk_start in range(0, len(ibox_no_list), fit_limit):
                outbox_no_counter += 1
                chunk = ibox_no_list[chunk_start: chunk_start + fit_limit]
                for ibox_no in chunk:
                    outbox_by_inbox[ibox_no] = (outbox_name, outbox_no_counter)

        for i, r in enumerate(daone_rows):
            ibox_no = packing_per_row[i].get('inbox_no')
            if ibox_no is not None:
                obox_name, obox_no = outbox_by_inbox.get(ibox_no, ('', None))
                packing_per_row[i]['outbox'] = obox_name
                packing_per_row[i]['outbox_no'] = obox_no

    # 행 출력 (인박스NO 순서로 정렬해 그룹이 모이게)
    if add_packing_columns:
        order = sorted(range(len(daone_rows)),
                       key=lambda i: (packing_per_row[i].get('inbox_no') or 9999, i))
    else:
        order = list(range(len(daone_rows)))

    for i in order:
        r = daone_rows[i]
        row_values = [r.get(h, '') for h in DAONE_HEADERS]
        if add_packing_columns:
            p = packing_per_row[i]
            row_values += [p.get('inbox_no'), p.get('inbox'),
                           p.get('outbox'), p.get('outbox_no')]
        ws.append(row_values)

    widths = [14, 18, 14, 14, 40, 14, 8, 12, 16, 16, 12, 16, 16, 12, 50, 50, 30, 16, 12]
    if add_packing_columns:
        widths = widths + [8, 22, 12, 10]
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


# ─── KSE OMS 주문내역 (큐텐 국내출고, 한국 KSE 경유) ───────────────────────
# KSE OMS 다운로드 양식 (26컬럼):
#   번호 / 등록일 / 접수번호 / 배송상태 / 배송타입 / 도착지송장번호 / 판매마켓 /
#   주문일 / 주문번호 / 장바구니번호 / 상품코드 / 판매자코드 /
#   상품명(판매마켓대표상품명) / 옵션명 / 옵션코드 / 금액 / 수량 /
#   받는사람 / 받는사람전화 / 우편번호 / 주소 / 사이즈 / 실무게 / 부피무게 / 적용무게 / RegionName

def parse_kse_oms_xlsx(data: bytes) -> List[Dict]:
    """KSE OMS 주문내역.xlsx bytes → 헤더 기반 dict 리스트.
    첫 시트의 row 1=헤더, row 2+=데이터.
    """
    import io as _io
    import openpyxl as _opx
    wb = _opx.load_workbook(_io.BytesIO(data), data_only=True)
    ws = wb.active
    if ws.max_row < 2:
        return []
    headers = [str(ws.cell(1, c).value).strip() if ws.cell(1, c).value is not None else ''
               for c in range(1, ws.max_column + 1)]
    rows = []
    for r in range(2, ws.max_row + 1):
        d = {}
        for c, h in enumerate(headers, 1):
            v = ws.cell(r, c).value
            if v is None:
                d[h] = ''
            elif isinstance(v, float) and v.is_integer():
                d[h] = str(int(v))
            else:
                d[h] = str(v)
        # 빈 행 건너뜀 (번호 컬럼이 비어있으면)
        if not d.get('번호') and not d.get('주문번호'):
            continue
        rows.append(d)
    return rows


def kse_oms_to_daone(kse_rows: List[Dict]) -> List[Dict]:
    """KSE OMS dict 리스트 → 다원 19컬럼 dict 리스트.
    SKU 매핑(제품코드)은 추후 단계에서 추가 — 현재는 빈값.
    """
    out = []
    for k in kse_rows:
        receiver = k.get('받는사람', '')
        phone = k.get('받는사람전화', '')
        addr = k.get('주소', '')
        zip_code = k.get('우편번호', '')
        name = k.get('상품명(판매마켓대표상품명)', '')
        option = k.get('옵션명', '')
        full_name = name + (' / ' + option if option else '')

        # 수량 정수
        try:
            qty = int(float(k.get('수량', 0))) if k.get('수량') else 0
        except (ValueError, TypeError):
            qty = 0

        d = {h: '' for h in DAONE_HEADERS}
        d['몰명(또는 몰코드)'] = DEFAULT_몰코드
        d['출하의뢰번호']     = k.get('판매마켓', '')
        d['출하의뢰항번']     = k.get('접수번호', '')
        d['고객주문번호']     = k.get('주문번호', '')
        d['상품명']           = full_name.strip()
        d['제품코드']         = ''  # SKU 매핑은 다음 단계
        d['주문수량']         = qty
        d['주문자명']         = receiver
        d['주문자연락처1']    = phone
        d['주문자연락처2']    = ''
        d['수취인명']         = receiver
        d['수취인연락처1']    = phone
        d['수취인연락처2']    = ''
        d['수취인우편번호']   = zip_code
        d['수취인주소1']      = addr
        d['주소2']            = addr  # 빈값 fallback 규칙 (수취인주소1 복사)
        d['배송메시지']       = ''
        d['송장번호']         = k.get('도착지송장번호', '')
        d['택배사명']         = k.get('배송타입', '')
        out.append(d)
    return out


def convert_kse_oms_to_daone(xlsx_bytes: bytes) -> tuple[bytes, int]:
    """원샷 변환 (매핑 미사용, 제품코드 빈값): KSE OMS xlsx → 다원 19컬럼 발주서.
    호환성용. 실제 운영에선 kse_oms_to_daone_with_mapping 사용 권장.
    """
    kse_rows = parse_kse_oms_xlsx(xlsx_bytes)
    daone_rows = kse_oms_to_daone(kse_rows)
    return build_daone_xlsx(daone_rows), len(daone_rows)


# KSE 한국 집하지 (다원이 큐텐 국내 출고 시 보내는 곳)
# 인박스에 KSE 송장(PDF) 부착 → 아웃박스로 합포장 → 이 집하지로 발송 → KSE 가 한국→일본 이동
KSE_KR_DEPOT = {
    'name':       'KSE',
    'phone':      '02 3143 5555',
    'zip':        '03917',
    'address':    '서울특별시 마포구 구룡길 36, (주)국제로지스틱 수색 EC 물류센터 내 G1 (GATE 22)',
    'msg':        'KSE',
}


def kse_oms_to_daone_with_mapping(kse_rows: List[Dict], mappings: Dict) -> Dict:
    """KSE OMS dict + Qoo10 매핑 → 다원 19컬럼 dict (SKU 환산 후).

    분기 (Qoo10 일본 빌더와 enabled 분기 반대):
      매핑 없음            → unknown_rows (등록 강제)
      매핑 + enabled=True  → not_for_daone_rows (KSE 일본 출고 대상, 다원 제외)
      매핑 + enabled=False, sku_codes 정상 → daone_rows 에 1→N 펼침
      매핑 + enabled=False, sku_codes='-'/빈 → incomplete_rows (다원 SKU 미입력)

    반환: dict {
        'daone_rows': [...], 'unknown_rows': [...],
        'not_for_daone_rows': [...], 'incomplete_rows': [...],
    }
    """
    daone_rows: List[Dict] = []
    unknown_rows: List[Dict] = []
    not_for_daone_rows: List[Dict] = []
    incomplete_rows: List[Dict] = []

    for k in kse_rows:
        name = (k.get('상품명(판매마켓대표상품명)') or '').strip()
        option = (k.get('옵션명') or '').strip()
        m = mappings.get((name, option))

        info = {
            '주문번호': k.get('주문번호', ''),
            '접수번호': k.get('접수번호', ''),
            '상품명': name,
            '옵션명': option,
            '수량': k.get('수량', ''),
        }

        if m is None:
            unknown_rows.append(info)
            continue
        if m.get('enabled'):
            not_for_daone_rows.append(info)
            continue
        # enabled=False = 다원 출고 대상
        valid = [(s.strip(), q) for s, q in zip(m.get('sku_codes', []), m.get('quantities', []))
                 if s and s.strip() and s.strip() != '-']
        if not valid:
            incomplete_rows.append(info)
            continue

        try:
            base_qty = int(float(k.get('수량', 1) or 1))
        except (ValueError, TypeError):
            base_qty = 1

        full_name = name + (' / ' + option if option else '')

        for sku_code, sku_unit in valid:
            try:
                unit = int(sku_unit)
            except (ValueError, TypeError):
                unit = 1
            d = {h: '' for h in DAONE_HEADERS}
            d['몰명(또는 몰코드)'] = DEFAULT_몰코드
            d['출하의뢰번호']     = k.get('판매마켓', '')
            d['출하의뢰항번']     = k.get('장바구니번호', '')
            d['고객주문번호']     = k.get('주문번호', '')
            d['상품명']           = full_name.strip()
            d['제품코드']         = sku_code
            d['주문수량']         = unit * base_qty
            # 패킹 그룹 키 보존 (도착지송장번호 + 장바구니번호 → 같은 인박스NO)
            d['_group_key']      = (str(k.get('도착지송장번호', '')),
                                    str(k.get('장바구니번호', '')))
            # 다원 → KSE 한국 집하지 고정 정보 (일본 고객 정보 아님)
            # 도착지송장번호 + KSE 송장 PDF 는 인박스에 부착되어 KSE 가 일본으로 이동.
            d['주문자명']         = KSE_KR_DEPOT['name']
            d['주문자연락처1']    = KSE_KR_DEPOT['phone']
            d['주문자연락처2']    = KSE_KR_DEPOT['phone']
            d['수취인명']         = KSE_KR_DEPOT['name']
            d['수취인연락처1']    = KSE_KR_DEPOT['phone']
            d['수취인연락처2']    = KSE_KR_DEPOT['phone']
            d['수취인우편번호']   = KSE_KR_DEPOT['zip']
            d['수취인주소1']      = KSE_KR_DEPOT['address']
            d['주소2']            = KSE_KR_DEPOT['address']
            d['배송메시지']       = KSE_KR_DEPOT['msg']
            # 송장번호/택배사명 은 다원이 채움 (한국 내 다원→KSE 집하지 운송 송장)
            d['송장번호']         = ''
            d['택배사명']         = ''
            daone_rows.append(d)

    return {
        'daone_rows': daone_rows,
        'unknown_rows': unknown_rows,
        'not_for_daone_rows': not_for_daone_rows,
        'incomplete_rows': incomplete_rows,
    }
