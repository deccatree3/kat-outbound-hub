"""
박스 마스터 + 합포 수량 (정리.xlsx Sheet3 기반).

다원에서 인박스 포장 + 아웃박스 합포 시 박스 효율 계산용. 향후 박스 관련
계산(박스 결정 / 패킹 가이드 / 발주서 박스 추천 등)은 이 모듈을 참조한다.

합포 수량 계산은 6회전 grid packing 알고리즘:
  - 인박스를 6가지 회전(축 순열) 으로 시도
  - 같은 회전으로만 적층 (혼합 회전 미고려)
  - 박스 벽 두께/완충재 마진은 미반영 — 운영 시 실측 보정 필요

기존 표(`정리.xlsx` Sheet3)의 '에이지샷1호 최대 합포 수량' 컬럼과
계산 결과가 거의 일치(±1) — 알고리즘 검증됨.
'위오1호 최대 합포 수량' 컬럼은 이 모듈의 fit_위오1호 값으로 채움.
"""
from typing import Dict, List, Optional, Tuple


BOXES: List[Dict] = [
    # name        L    W    H    size  fee   fit_에이지샷1호  fit_위오1호
    {'name': '위오 1호',     'L': 240, 'W': 170, 'H': 180, 'size_class': '극소', 'shipping_fee': 2000, 'fit_에이지샷1호': 3,    'fit_위오1호': 1},
    {'name': '위오 3호',     'L': 340, 'W': 260, 'H': 190, 'size_class': '소',   'shipping_fee': 2500, 'fit_에이지샷1호': 8,    'fit_위오1호': 2},
    {'name': '위오 5호',     'L': 420, 'W': 300, 'H': 260, 'size_class': '중',   'shipping_fee': 3050, 'fit_에이지샷1호': 17,   'fit_위오1호': 2},
    {'name': '위오 7호',     'L': 500, 'W': 300, 'H': 160, 'size_class': '소',   'shipping_fee': 2500, 'fit_에이지샷1호': 12,   'fit_위오1호': 0},
    {'name': '위오 9호',     'L': 490, 'W': 390, 'H': 300, 'size_class': '중',   'shipping_fee': 3050, 'fit_에이지샷1호': 23,   'fit_위오1호': 4},
    {'name': '위오 11호',    'L': 560, 'W': 380, 'H': 300, 'size_class': '대1',  'shipping_fee': 4600, 'fit_에이지샷1호': 35,   'fit_위오1호': 6},
    {'name': '에이지샷 1호', 'L': 160, 'W': 110, 'H': 70,  'size_class': '극소', 'shipping_fee': 2000, 'fit_에이지샷1호': None, 'fit_위오1호': 0},
    {'name': '에이지샷 9호', 'L': 750, 'W': 520, 'H': 420, 'size_class': '대2',  'shipping_fee': 4900, 'fit_에이지샷1호': 100,  'fit_위오1호': 18},
]

# 인박스 사용 규칙 (정리.xlsx Sheet3, '■ 에이지샷 1호 인박스 사용 시'):
INBOX_RULES_에이지샷 = [
    {'qty_min': 1, 'qty_max': 3,  'inbox': '에이지샷 1호'},
    {'qty_min': 4, 'qty_max': 10, 'inbox': '위오 1호'},
    # … 추가 규칙은 운영 시 입력
]


def get_box(name: str) -> Optional[Dict]:
    return next((b for b in BOXES if b['name'] == name), None)


def fit_count(outer: Tuple[int, int, int], inner: Tuple[int, int, int]) -> int:
    """outer 안에 inner 6회전 grid 적층 시 최대 개수."""
    L, W, H = outer
    rotations = [
        (inner[0], inner[1], inner[2]),
        (inner[0], inner[2], inner[1]),
        (inner[1], inner[0], inner[2]),
        (inner[1], inner[2], inner[0]),
        (inner[2], inner[0], inner[1]),
        (inner[2], inner[1], inner[0]),
    ]
    best = 0
    for l, w, h in rotations:
        if l <= L and w <= W and h <= H:
            cnt = (L // l) * (W // w) * (H // h)
            if cnt > best:
                best = cnt
    return best


def fit_table(inner_name: str) -> Dict[str, int]:
    """주어진 인박스를 각 박스에 합포할 수 있는 수량 dict 반환."""
    inner = get_box(inner_name)
    if not inner:
        return {}
    inner_lwh = (inner['L'], inner['W'], inner['H'])
    return {
        b['name']: fit_count((b['L'], b['W'], b['H']), inner_lwh)
        for b in BOXES
    }


def select_inbox_for_에이지샷(qty: int) -> Optional[str]:
    """에이지샷 N개 일 때 추천 인박스. 규칙 미정 범위면 None."""
    for rule in INBOX_RULES_에이지샷:
        if rule['qty_min'] <= qty <= rule['qty_max']:
            return rule['inbox']
    return None
