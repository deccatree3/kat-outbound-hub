"""
KSE 국내 출고 부착문서 PDF — 아웃박스에 부착하는 라벨.

샘플(`출고/KSE 국내/시스템 최종 결과물/캐처스 - KSE 국내 - KSE_부착문서_*.pdf`):
  페이지 1개 = 아웃박스 1개. 표 (3행 2열):
    | 업체명     | 캐처스                  |
    | 발송일     | 2026-02-12              |
    | 입수량     | 20EA                    |
    |            | (OUTBOX : 위오11호 / 아웃박스NO.1) |

입력: 패킹 정보가 부여된 daone_rows (compute_packing 결과 또는 build_daone_xlsx 통과 후).
출력: 아웃박스NO 순서로 페이지 N개 PDF bytes.
"""
import datetime
import io
import os
from collections import OrderedDict
from typing import List, Dict

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, PageBreak, Paragraph


_FONT_REGISTERED = None


def _register_korean_font() -> str:
    """한글 폰트 등록 (시스템 폰트 검색). 등록된 폰트명 반환."""
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return _FONT_REGISTERED

    candidates = [
        # Windows
        (r'C:\Windows\Fonts\malgunbd.ttf', 'MalgunBold'),
        (r'C:\Windows\Fonts\malgun.ttf', 'Malgun'),
        # Linux (NanumGothic — Streamlit Cloud 의 packages.txt 에 fonts-nanum 추가 시)
        ('/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf', 'NanumGothicBold'),
        ('/usr/share/fonts/truetype/nanum/NanumGothic.ttf', 'NanumGothic'),
        # macOS
        ('/System/Library/Fonts/AppleSDGothicNeo.ttc', 'AppleSDGothicNeo'),
        # 프로젝트 번들
        (os.path.join(os.path.dirname(__file__), 'NanumGothicBold.ttf'), 'NanumGothicBold'),
        (os.path.join(os.path.dirname(__file__), 'NanumGothic.ttf'), 'NanumGothic'),
    ]
    for path, name in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                _FONT_REGISTERED = name
                return name
            except Exception:
                continue
    # 폴백: 기본 폰트 (한글 깨짐 — 운영 환경에 폰트 누락 알림용)
    _FONT_REGISTERED = 'Helvetica'
    return 'Helvetica'


def _group_outboxes(packed_rows: List[Dict]) -> List[Dict]:
    """패킹 정보 부여된 daone_rows 를 (아웃박스명, 아웃박스NO) 별 그룹으로 묶음.
    반환: [{outbox_name, outbox_no, qty}, ...] (아웃박스NO 오름차순)
    """
    groups = OrderedDict()
    for r in packed_rows:
        obox_no = r.get('_packing_outbox_no')
        obox_name = r.get('_packing_outbox')
        if obox_no is None or not obox_name:
            continue
        key = (obox_no, obox_name)
        if key not in groups:
            groups[key] = {
                'outbox_name': obox_name,
                'outbox_no': obox_no,
                'qty': 0,
            }
        try:
            groups[key]['qty'] += int(r.get('주문수량', 0) or 0)
        except (ValueError, TypeError):
            pass
    return sorted(groups.values(), key=lambda g: g['outbox_no'])


def build_kse_attached_pdf(packed_rows: List[Dict],
                           work_date: datetime.date,
                           company: str = '캐처스') -> bytes:
    """KSE 부착문서 PDF — 아웃박스 1개 = 페이지 1개."""
    font = _register_korean_font()
    bold = font  # 동일 폰트 사용 (별도 Bold 등록 시 변경)

    outboxes = _group_outboxes(packed_rows)
    if not outboxes:
        # 빈 경우라도 안내 페이지 1장 반환
        outboxes = [{'outbox_name': '(없음)', 'outbox_no': 0, 'qty': 0}]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )
    elements = []

    date_str = work_date.strftime('%Y-%m-%d')

    qty_style = ParagraphStyle(
        name='qty', fontName=font, fontSize=44, leading=52, alignment=1,  # 1=CENTER
    )

    for i, g in enumerate(outboxes):
        outbox_name_compact = (g['outbox_name'] or '').replace(' ', '')
        # 3줄: 큰 NEA + 작은 OUTBOX 라인 + 작은 아웃박스NO 라인
        qty_html = (
            f"{g['qty']}EA<br/>"
            f'<font size="22">OUTBOX: {outbox_name_compact}</font><br/>'
            f'<font size="22">아웃박스NO: {g["outbox_no"]}</font>'
        )
        qty_para = Paragraph(qty_html, qty_style)

        data = [
            ['업체명', company],
            ['발송일\n(택배출하일)', date_str],
            ['입수량', qty_para],
        ]
        # 컬럼 너비: 좌측 라벨 80mm / 우측 값 160mm
        tbl = Table(data, colWidths=[80*mm, 160*mm], rowHeights=[40*mm, 40*mm, 80*mm])
        tbl.setStyle(TableStyle([
            ('FONT', (0, 0), (-1, -1), font, 24),
            ('FONT', (0, 0), (0, -1), bold, 28),
            ('FONTSIZE', (1, 0), (1, 1), 36),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 1.2, colors.black),
        ]))
        elements.append(tbl)
        if i < len(outboxes) - 1:
            elements.append(PageBreak())

    doc.build(elements)
    return buf.getvalue()
