"""물류창고(WMS) 재고현황 파일 파서. 2 포맷 자동 감지 지원.

지원 포맷:
1) **다원 WMS (Document_*.xls)** — 레거시. 헤더에 `품목코드`, `가능수량` 존재.
2) **태영종합물류 (현재고_*.xls)** — 신규(2026-07). 헤더에 `상품코드`, `가용재고` 존재.

동일 바코드의 여러 행은 LOC/LOT 단위 분할. 유통일이 다르면 **독립 배치**로 취급.

태영 신규 포맷 특이사항:
- 로트번호 비어있는 행 = "부족재고 요약행" (현재고=-부족재고). **무시**.
- RELEASEAREA 필터 개념 없음 — `가용재고` 컬럼에 이미 반영됨 (WMS 계산치).
- 소비기한 = 'YYYYMMDD' 문자열 (엑셀 serial 아님).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import xlrd

from .base import WmsInventoryRow, WmsSnapshot


def _to_int(v: Any) -> int | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _to_str_opt(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _excel_serial_to_date(v: Any, book_datemode: int) -> date | None:
    """엑셀 serial 날짜 → date. 문자열로 온 경우도 대응."""
    if v is None or v == "" or v == "-":
        return None
    if isinstance(v, (int, float)):
        if v <= 0:
            return None
        try:
            y, m, d, _, _, _ = xlrd.xldate_as_tuple(v, book_datemode)
            return date(y, m, d)
        except Exception:
            return None
    if isinstance(v, str):
        s = v.strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    return None


_DATE_IN_NAME_LEGACY = re.compile(r"Document_(\d{4})-(\d{2})-(\d{2})")
_DATE_IN_NAME_NEW = re.compile(r"(\d{4})(\d{2})(\d{2})_\d+")


def _infer_snapshot_date(filename: str) -> date:
    m = _DATE_IN_NAME_LEGACY.search(filename)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = _DATE_IN_NAME_NEW.search(filename)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return date.today()


def _yyyymmdd_to_date(v: Any) -> date | None:
    """'YYYYMMDD' 문자열 → date. 태영 신규 포맷 소비기한/제조일자용."""
    if v is None or v == "" or v == "-":
        return None
    s = str(v).strip()
    if not s or s == "0":
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


_HEADER_ALIASES_LEGACY = {
    "barcode": ["품목코드"],
    "product_name": ["품목명"],
    "loc_group": ["LOC그룹"],
    "loc": ["LOC"],
    "total_qty": ["재고수량"],
    "alloc_qty": ["할당수량"],
    "available_qty": ["가능수량"],
    "expiry": ["속성5(유통일)", "속성5", "유통일"],
}

_HEADER_ALIASES_NEW = {
    "barcode": ["상품코드"],
    "product_name": ["상품명"],
    "warehouse_zone": ["창고존"],
    "loc": ["로케이션 코드"],
    "lot_no": ["로트번호"],
    "manufacture_date": ["제조일자"],
    "expiry": ["소비기한"],
    "total_qty": ["현재고"],
    "wip_qty": ["작업중재고"],
    "reserved_qty": ["출고예정지정재고"],
    "available_qty": ["가용재고"],
    "shortage_qty": ["부족재고"],
    "owner": ["화주"],
}


def _resolve_headers(header_row: list, aliases: dict) -> dict[str, int]:
    """헤더 row 를 읽어서 필드 → 컬럼 인덱스 매핑 반환."""
    lookup: dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        name = str(cell).strip() if cell is not None else ""
        if not name:
            continue
        for field, alias_list in aliases.items():
            if name in alias_list and field not in lookup:
                lookup[field] = idx
    return lookup


def _detect_format(header_row: list) -> str:
    """헤더 시그니처로 포맷 판별. 'legacy'(다원) / 'new'(태영) / 'unknown'."""
    names = {str(c).strip() for c in header_row if c is not None}
    if "상품코드" in names and "가용재고" in names:
        return "new"
    if "품목코드" in names and "가능수량" in names:
        return "legacy"
    return "unknown"


def parse_wms_inventory_file(path: str | Path) -> WmsSnapshot:
    """WMS 재고현황 xls 파싱 → WmsSnapshot. 다원/태영 포맷 자동 감지.

    각 raw row = 1 LOC/LOT. expiry_short 에 배치 유통일을 넣는다.
    태영 신규 포맷은 로트번호 빈 행(부족재고 요약행)은 제외한다.
    """
    path = Path(path)
    wb = xlrd.open_workbook(str(path))
    ws = wb.sheet_by_index(0)
    datemode = wb.datemode

    header = ws.row_values(0) if ws.nrows > 0 else []
    fmt = _detect_format(header)

    if fmt == "new":
        rows = _parse_new_format(ws, header)
    else:
        # legacy 또는 unknown — 기존 파서로 폴백
        rows = _parse_legacy_format(ws, header, datemode)

    return WmsSnapshot(
        snapshot_date=_infer_snapshot_date(path.name),
        source_file=path.name,
        rows=rows,
    )


def _parse_legacy_format(ws, header: list, datemode: int) -> list[WmsInventoryRow]:
    """다원 WMS Document_*.xls 파서 (기존 로직 유지)."""
    cols = _resolve_headers(header, _HEADER_ALIASES_LEGACY)

    def _get(row: list, field: str, fallback_idx: int):
        idx = cols.get(field, fallback_idx)
        return row[idx] if 0 <= idx < len(row) else None

    rows: list[WmsInventoryRow] = []
    for i in range(1, ws.nrows):
        r = ws.row_values(i)
        barcode = _to_str_opt(_get(r, "barcode", 0))
        if not barcode:
            continue

        rows.append(WmsInventoryRow(
            barcode=barcode,
            product_name=_to_str_opt(_get(r, "product_name", 1)),
            loc_group=_to_str_opt(_get(r, "loc_group", 3)),
            loc=_to_str_opt(_get(r, "loc", 5)),
            total_qty=_to_int(_get(r, "total_qty", 6)),
            alloc_qty=_to_int(_get(r, "alloc_qty", 7)),
            available_qty=_to_int(_get(r, "available_qty", 11)),
            expiry_short=_excel_serial_to_date(_get(r, "expiry", 17), datemode),
            expiry_long=None,
            raw={str(j): (str(v) if v not in (None, "") else None) for j, v in enumerate(r)},
        ))
    return rows


def _parse_new_format(ws, header: list) -> list[WmsInventoryRow]:
    """태영 신규 포맷 파서.

    규칙:
    - 로트번호 비어있는 행 = 부족재고 요약행 → 스킵
    - available_qty = `가용재고` (RELEASEAREA 필터 별도 불필요)
    - alloc_qty = 작업중재고 + 출고예정지정재고 (물리적으로 배정된 재고 합)
    - expiry_short = `소비기한` (YYYYMMDD 문자열)
    - loc_group = `창고존`
    """
    cols = _resolve_headers(header, _HEADER_ALIASES_NEW)

    def _get(row: list, field: str):
        idx = cols.get(field)
        return row[idx] if idx is not None and 0 <= idx < len(row) else None

    rows: list[WmsInventoryRow] = []
    for i in range(1, ws.nrows):
        r = ws.row_values(i)
        barcode = _to_str_opt(_get(r, "barcode"))
        if not barcode:
            continue

        lot_no = _to_str_opt(_get(r, "lot_no"))
        if not lot_no:
            # 로트번호 빈 행 = 부족재고 요약행 (현재고 = -부족재고). 재고로 취급하지 않음.
            continue

        wip = _to_int(_get(r, "wip_qty")) or 0
        reserved = _to_int(_get(r, "reserved_qty")) or 0

        rows.append(WmsInventoryRow(
            barcode=barcode,
            product_name=_to_str_opt(_get(r, "product_name")),
            loc_group=_to_str_opt(_get(r, "warehouse_zone")),
            loc=_to_str_opt(_get(r, "loc")),
            total_qty=_to_int(_get(r, "total_qty")),
            alloc_qty=wip + reserved,
            available_qty=_to_int(_get(r, "available_qty")),
            expiry_short=_yyyymmdd_to_date(_get(r, "expiry"))
                        or _yyyymmdd_to_date(_get(r, "manufacture_date")),
            expiry_long=None,
            raw={str(j): (str(v) if v not in (None, "") else None) for j, v in enumerate(r)},
        ))
    return rows


#: LOC 이 이 값인 행은 가능재고에서 제외 (이미 출고 대기/피킹 완료 상태)
EXCLUDED_LOCS = {"RELEASEAREA"}


def aggregate_wms_by_barcode(
    snapshot: WmsSnapshot,
    excluded_locs: set[str] = EXCLUDED_LOCS,
) -> dict[str, dict[str, Any]]:
    """바코드별 요약 + **유통일 기준 배치 리스트** 반환.

    LOC ∈ `excluded_locs` (기본: RELEASEAREA) 인 행은 **가능재고에 포함되지 않는다**.
    총재고(total_qty) / 배치 total 에도 반영하지 않는다 (이미 출고 절차에 들어간 재고).

    배치(batch) = 동일 유통일을 공유하는 행들의 가용수량 합계.
    LOC 그룹은 무시하고 expiry_date 만으로 그룹화한다.

    Returns:
        {
            barcode: {
                "total_qty": int,                 # 전체 재고수량
                "available_qty": int,             # 전체 가용수량
                "alloc_qty": int,                 # 전체 할당수량
                "product_name": str|None,
                "batches": [                      # expiry_date 오름차순
                    {"expiry": date, "available": int, "total": int},
                    ...
                ],
                "expiry_short": date|None,        # 가장 빠른 유통일 (호환용)
                "expiry_long": date|None,         # 가장 늦은 유통일 (호환용)
            }
        }
    """
    # (barcode, expiry_date) 단위 집계
    batch_map: dict[tuple[str, Any], dict[str, int]] = {}
    total_map: dict[str, dict[str, Any]] = {}

    excluded_norm = {s.strip().upper() for s in excluded_locs}

    for row in snapshot.rows:
        if not row.barcode:
            continue
        # 제외 LOC 필터 (RELEASEAREA 등)
        if row.loc and row.loc.strip().upper() in excluded_norm:
            continue

        t = total_map.setdefault(
            row.barcode,
            {
                "total_qty": 0,
                "available_qty": 0,
                "alloc_qty": 0,
                "product_name": row.product_name,
            },
        )
        t["total_qty"] += row.total_qty or 0
        t["available_qty"] += row.available_qty or 0
        t["alloc_qty"] += row.alloc_qty or 0

        # 배치 키: (barcode, expiry). expiry가 없으면 None → '미표시' 배치
        key = (row.barcode, row.expiry_short)
        b = batch_map.setdefault(key, {"available": 0, "total": 0})
        b["available"] += row.available_qty or 0
        b["total"] += row.total_qty or 0

    # barcode → batches 리스트
    agg: dict[str, dict[str, Any]] = {}
    for (barcode, expiry), qtys in batch_map.items():
        a = agg.setdefault(
            barcode,
            {
                **total_map[barcode],
                "batches": [],
                "expiry_short": None,
                "expiry_long": None,
            },
        )
        a["batches"].append({"expiry": expiry, "available": qtys["available"], "total": qtys["total"]})

    for barcode, a in agg.items():
        # 유통일 오름차순 (None 은 맨 뒤)
        a["batches"].sort(key=lambda b: (b["expiry"] is None, b["expiry"]))
        dated = [b for b in a["batches"] if b["expiry"] is not None]
        if dated:
            a["expiry_short"] = dated[0]["expiry"]
            a["expiry_long"] = dated[-1]["expiry"]
    return agg
