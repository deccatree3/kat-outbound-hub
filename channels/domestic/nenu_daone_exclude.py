"""[네뉴] 출고요청 발주서에서 제외할 상품 목록 (임시-다원출고).

배경 (2026-07 임시방편):
    물류창고 이전 (다원 → 태영) 진행 중, 일부 품목 재고가 이동 안 되어
    이 목록의 상품은 계속 다원에서 출고. 나머지는 태영 출고 대상.

    이전 완료되면 이 리스트를 비우면 자동 롤백.

원본 참조: C:/Users/decca/Desktop/태영물류/임시-다원출고.xlsx
"""
from __future__ import annotations

# (바코드, 상품명) — 상품명은 참조용, 매칭은 바코드로만
NENU_DAONE_EXCLUDE: list[tuple[str, str]] = [
    ("8809744300078", "퍼펙토 맥시멈 블랙마카"),
    ("8809647580140", "데일리키토 방탄커피(14포)"),
    ("8809647580041", "닥터키토 방탄커피(10포)"),
    ("8809647580126", "스키니퓨리티 슈링티(30T)"),
]


def excluded_barcodes() -> set[str]:
    """제외 대상 바코드 세트. 매칭용."""
    return {str(bc).strip() for bc, _ in NENU_DAONE_EXCLUDE if bc}


def excluded_names_by_barcode() -> dict[str, str]:
    """{바코드: 상품명} 매핑 (UI 표시용)."""
    return {str(bc).strip(): str(nm) for bc, nm in NENU_DAONE_EXCLUDE if bc}
