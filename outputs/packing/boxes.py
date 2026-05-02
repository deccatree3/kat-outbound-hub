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


# ─── 큐텐 국내 (KSE) 패킹 알고리즘 ──────────────────────────────────────

def split_to_inboxes(qty: int) -> List[Tuple[str, int]]:
    """qty 개의 에이지샷을 인박스로 분할. [(인박스종류, 담은 수량), ...] 반환.
    규칙: 1~3개 → 에이지샷 1호 / 4~10개 → 위오 1호. 11+ 면 위 규칙을 반복(분할).
    """
    out: List[Tuple[str, int]] = []
    remaining = int(qty or 0)
    while remaining > 0:
        if remaining >= 4:
            n = min(10, remaining)
            out.append(('위오 1호', n))
        else:
            n = min(3, remaining)
            out.append(('에이지샷 1호', n))
        remaining -= n
    return out


# 아웃박스 후보 (에이지샷 1호는 인박스 전용이라 제외, 배송비 오름차순)
def _outbox_candidates(inbox_type: str) -> List[Dict]:
    fit_key = 'fit_에이지샷1호' if inbox_type == '에이지샷 1호' else 'fit_위오1호'
    return sorted(
        [b for b in BOXES if b['name'] != '에이지샷 1호' and b.get(fit_key)],
        key=lambda x: x['shipping_fee'],
    )


def select_outbox_for(inbox_type: str, count: int) -> Tuple[str, int]:
    """주어진 인박스 N개를 담을 가장 저렴한 아웃박스 (Best-Fit).
    반환: (아웃박스명, 그 박스의 fit 한도). count > 모든 박스 fit 이면 가장 큰 fit 박스 반환.
    """
    fit_key = 'fit_에이지샷1호' if inbox_type == '에이지샷 1호' else 'fit_위오1호'
    candidates = _outbox_candidates(inbox_type)
    if not candidates:
        return ('', 0)
    box = next((b for b in candidates if b[fit_key] >= count), None)
    if box is None:
        box = max(candidates, key=lambda x: x[fit_key])
    return (box['name'], box[fit_key])


def compute_packing(daone_rows: List[Dict],
                    group_key_field: str = '_group_key') -> List[Dict]:
    """daone_rows 에 패킹 컬럼 4개를 mutating 추가 + 인박스NO 순으로 정렬해서 반환.

    각 행에 추가되는 키:
      _packing_inbox      (예: '에이지샷 1호')
      _packing_inbox_no   (1, 2, 3...)
      _packing_outbox     (예: '위오 1호')
      _packing_outbox_no  (1, 2, 3...)
    """
    from collections import OrderedDict, defaultdict

    # 1) group_key 별로 그룹화
    groups = OrderedDict()
    for i, r in enumerate(daone_rows):
        key = r.get(group_key_field) or (i,)
        groups.setdefault(key, []).append(i)

    # 2) 그룹별 인박스 결정 + 인박스NO 부여
    inbox_no_to_label = {}
    for inbox_no, (gk, idxs) in enumerate(groups.items(), 1):
        total_qty = sum(int(daone_rows[i].get('주문수량', 0) or 0) for i in idxs)
        split = split_to_inboxes(total_qty)
        if len(split) == 1:
            label = split[0][0]
        else:
            label = ' + '.join(f"{t}×{n}" for t, n in split) + ' (분할)'
        inbox_no_to_label[inbox_no] = label
        for i in idxs:
            daone_rows[i]['_packing_inbox'] = label
            daone_rows[i]['_packing_inbox_no'] = inbox_no

    # 3) 인박스 종류별로 모아 아웃박스 결정 (Best-Fit)
    inbox_nos_by_label = defaultdict(list)
    for ibox_no, label in inbox_no_to_label.items():
        inbox_nos_by_label[label].append(ibox_no)

    outbox_by_inbox = {}
    outbox_no_counter = 0
    for label, ibox_no_list in inbox_nos_by_label.items():
        primary = label.split('×')[0].split(' + ')[0].strip()
        outbox_name, fit_limit = select_outbox_for(primary, len(ibox_no_list))
        if not outbox_name:
            outbox_name, fit_limit = ('미정', max(1, len(ibox_no_list)))
        for chunk_start in range(0, len(ibox_no_list), fit_limit):
            outbox_no_counter += 1
            chunk = ibox_no_list[chunk_start: chunk_start + fit_limit]
            for ibox_no in chunk:
                outbox_by_inbox[ibox_no] = (outbox_name, outbox_no_counter)

    for i, r in enumerate(daone_rows):
        ibox_no = r.get('_packing_inbox_no')
        if ibox_no is not None:
            obox_name, obox_no = outbox_by_inbox.get(ibox_no, ('', None))
            r['_packing_outbox'] = obox_name
            r['_packing_outbox_no'] = obox_no

    # 4) 인박스NO 순 정렬
    return sorted(daone_rows,
                  key=lambda r: (r.get('_packing_inbox_no') or 9999,))
