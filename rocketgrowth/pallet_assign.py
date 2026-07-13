"""팔레트 배분 알고리즘 — Split-First-then-Minimize-Bins.

규칙:
1) SKU 박스수 내림차순 정렬 (동률은 상품명 가나다 순)
2) 박스수 ≥ 팔레트 용량 인 SKU 는 단독 팔레트로 분할
   - 잔여(< 용량) 는 잔여풀로
3) 잔여풀은 **팔레트 수를 최소화**하도록 적재 (박스합 하한부터 백트래킹 탐색)
   - 팔레트당 박스합 ≤ 용량
   - 같은 SKU(잔여 아이템) 는 통째로 한 팔레트에만 (무분할)
4) 탐색 예산 초과(비정상적으로 큰 입력) 시에만 기존 First-Fit 그리디로 폴백

과거엔 잔여풀을 "한 팔레트씩 큰 것부터 꽉 채우는" First-Fit 로 처리했으나,
큰 박스가 팔레트 입구를 막으면 팔레트를 1개 더 만드는 최적성 결함이 있었다.
예) 38박스(7·6·5 + 3·3·3·3 + 2·2·2·2) → First-Fit 은 18+18+2 = 3팔레트로 세지만
실제 최적은 19+19 = 2팔레트(7+6+3+3 / 5+3+3+2+2+2+2). 쿠팡 부착문서/발주 요약과
어긋나 검수에서 오탐이 발생 → 최소-팔레트 탐색으로 교정.

(`팔레트적재리스트` 같은 출력에서 1개 SKU 가 여러 행에 나뉘는 건
 박스수 ≥ 용량 분할 결과뿐이며, 잔여풀 분할은 발생하지 않는다.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PalletItem:
    """입력 아이템."""

    key: Any           # 식별자 (예: coupang_option_id)
    name: str          # 상품명 (정렬·라벨용)
    boxes: int         # 박스수
    extras: dict[str, Any] = field(default_factory=dict)  # 부가 정보 통과용


@dataclass
class PalletEntry:
    """팔레트 안에 들어가는 하나의 SKU 행."""

    key: Any
    name: str
    boxes: int
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class PalletAssignment:
    pallets: list[list[PalletEntry]]   # 팔레트별 entry 리스트 (1-indexed 의미)
    total_boxes: int
    pallet_count: int

    def pallet_no_of(self, key: Any) -> list[int]:
        """key 의 SKU 가 속한 팔레트 번호(1-indexed) 들."""
        result = []
        for i, p in enumerate(self.pallets, start=1):
            if any(e.key == key for e in p):
                result.append(i)
        return result


# 최소-팔레트 백트래킹 탐색 노드 예산. 초과 시 그리디 폴백.
# 실 밀크런은 잔여 SKU 수십 개 이하라 실제로는 거의 즉시 종료된다.
_PACK_SEARCH_BUDGET = 500_000


def _greedy_first_fit(items: list[PalletItem], cap: int) -> list[list[PalletItem]]:
    """기존 동작 — 한 팔레트씩 큰 것부터 채우는 First-Fit. 폴백 전용."""
    pool = list(items)
    bins: list[list[PalletItem]] = []
    while pool:
        current: list[PalletItem] = []
        used = 0
        while True:
            picked = None
            for idx, it in enumerate(pool):
                if used + int(it.boxes) <= cap:
                    picked = idx
                    break
            if picked is None:
                break
            it = pool.pop(picked)
            current.append(it)
            used += int(it.boxes)
        if current:
            bins.append(current)
        else:
            break
    return bins


def _pack_min_bins(items: list[PalletItem], cap: int) -> list[list[PalletItem]] | None:
    """잔여풀을 최소 팔레트 수로 적재.

    박스합 하한(ceil(sum/cap))부터 팔레트 수 k 를 늘려가며 k개로 담을 수 있는지
    백트래킹. 대칭 가지치기(동일 적재량 팔레트 스킵)로 작은 입력에선 즉시 끝난다.

    Returns:
        팔레트별 아이템 리스트. 탐색 예산 초과 시 None (호출자가 그리디 폴백).
    """
    n = len(items)
    if n == 0:
        return []
    sizes = [int(it.boxes) for it in items]
    # 내림차순 배치가 가지치기에 유리
    order = sorted(range(n), key=lambda i: -sizes[i])
    total = sum(sizes)
    lower_bound = -(-total // cap)  # ceil

    for k in range(lower_bound, n + 1):
        assign = [-1] * n
        loads = [0] * k
        counter = [0]

        def bt(pos: int) -> bool | None:
            if pos == n:
                return True
            counter[0] += 1
            if counter[0] > _PACK_SEARCH_BUDGET:
                return None  # 예산 초과 신호 — 폴백
            i = order[pos]
            sz = sizes[i]
            seen_loads: set[int] = set()  # 동일 적재량 팔레트는 한 번만 (대칭 제거)
            for b in range(k):
                load = loads[b]
                if load in seen_loads:
                    continue
                seen_loads.add(load)
                if load + sz <= cap:
                    loads[b] += sz
                    assign[i] = b
                    r = bt(pos + 1)
                    if r is True:
                        return True
                    if r is None:
                        return None
                    loads[b] -= sz
                    assign[i] = -1
            return False

        result = bt(0)
        if result is True:
            bins: list[list[PalletItem]] = [[] for _ in range(k)]
            for i in range(n):
                bins[assign[i]].append(items[i])
            # 꽉 찬 팔레트가 앞에 오도록 (출력 가독성). 동률은 첫 아이템 상품명.
            bins.sort(key=lambda bl: (-sum(int(it.boxes) for it in bl),
                                      bl[0].name if bl else ""))
            return bins
        if result is None:
            return None  # 예산 초과
    return None


def assign_pallets(items: list[PalletItem], pallet_size: int = 19) -> PalletAssignment:
    """팔레트 배분 수행.

    Args:
        items: 박스수 > 0 인 SKU 들
        pallet_size: 팔레트당 최대 박스수 (기본 19)

    Returns: PalletAssignment
    """
    if pallet_size <= 0:
        raise ValueError("pallet_size 는 양수여야 합니다")

    valid = [it for it in items if it.boxes and it.boxes > 0]
    if not valid:
        return PalletAssignment(pallets=[], total_boxes=0, pallet_count=0)

    # 1) 박스수 내림차순 정렬 (동률은 상품명)
    sorted_items = sorted(valid, key=lambda it: (-int(it.boxes), it.name or ""))

    pallets: list[list[PalletEntry]] = []
    leftover: list[PalletItem] = []

    # 2) 단독 팔레트 분할 (박스수 ≥ pallet_size)
    for it in sorted_items:
        boxes = int(it.boxes)
        if boxes >= pallet_size:
            full = boxes // pallet_size
            rem = boxes % pallet_size
            for _ in range(full):
                pallets.append(
                    [PalletEntry(key=it.key, name=it.name, boxes=pallet_size, extras=dict(it.extras))]
                )
            if rem > 0:
                leftover.append(
                    PalletItem(key=it.key, name=it.name, boxes=rem, extras=dict(it.extras))
                )
        else:
            leftover.append(PalletItem(key=it.key, name=it.name, boxes=boxes, extras=dict(it.extras)))

    # 3) 잔여풀 최적 적재 (팔레트 수 최소화). 4) 예산 초과 시 그리디 폴백.
    leftover.sort(key=lambda it: (-int(it.boxes), it.name or ""))
    packed = _pack_min_bins(leftover, pallet_size)
    if packed is None:
        packed = _greedy_first_fit(leftover, pallet_size)
    for bin_items in packed:
        pallets.append([
            PalletEntry(key=it.key, name=it.name, boxes=int(it.boxes), extras=dict(it.extras))
            for it in bin_items
        ])

    total_boxes = sum(e.boxes for p in pallets for e in p)
    return PalletAssignment(pallets=pallets, total_boxes=total_boxes, pallet_count=len(pallets))
