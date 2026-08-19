"""물류창고(WMS) 재고현황 파일 파서. 2 포맷 자동 감지 지원.

지원 포맷:
1) **다원 WMS (Document_*.xls)** — 레거시. 헤더에 `품목코드`, `가능수량` 존재.
2) **태영종합물류 (현재고_*.xls)** — 신규(2026-07). 헤더에 `상품코드`, `가용재고` 존재.

동일 바코드의 여러 행은 LOC/LOT 단위 분할. 유통일이 다르면 **독립 배치**로 취급.

태영 신규 포맷 특이사항:
- 재고 채택은 **현재고 값**으로만 판정한다: 현재고 > 0 → 실재고 채택 / 현재고 ≤ 0
  (= -부족재고) → 부족표시행이라 제외. **로트번호·제조일자·소비기한은 채택 판정에
  쓰지 않는다** (2026-08-16 고정).
- 재고 있는 행에 로트/제조일/소비기한이 비었거나 같은 상품이 중복 기재된 건
  태영 WMS 입력 문제 — 파서로 보정하지 않고 raw 데이터(현재고 파일) 정정으로 해결.
- RELEASEAREA 필터 개념 없음 — `가용재고` 컬럼에 이미 반영됨 (WMS 계산치).
- 소비기한 = 'YYYYMMDD' 문자열 (엑셀 serial 아님).

⚠️ **소비기한은 `소비기한` 컬럼에서만 읽는다.** 과거 이 파서는 소비기한이 비면
`제조일자` 컬럼 값으로 대체했으나, 그건 서로 다른 의미의 날짜를 소비기한으로
둔갑시키는 것이라 위험하다 (2026-07-20 제거). 소비기한이 없으면 `expiry_short=None`
으로 두고, 화면에서 "소비기한 정보 없음" 얼럿을 띄운다.
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
    태영 신규 포맷은 현재고 ≤ 0 인 부족표시행만 제외한다 (로트/날짜 미사용).
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

    재고 채택 원칙 (단순·고정): **현재고 > 0 인 행 = 실재고 → 그대로 채택.**
    - 현재고 ≤ 0 (= -부족재고) 인 행 = 부족표시행 → 재고 아님 → 스킵.
    - 로트번호·제조일자·소비기한은 **채택 판정에 쓰지 않는다.**
    - available_qty = `가용재고` (RELEASEAREA 필터 불필요 — WMS 계산치)
    - alloc_qty = 작업중재고 + 출고예정지정재고
    - expiry_short = `소비기한` (YYYYMMDD). 없으면 None — 제조일자로 대체 금지.
    - loc_group = `창고존`

    ⚠️ 이 파서는 태영 파일 값을 **그대로 반영만** 한다. 재고 있는 행에 로트/제조일/
    소비기한이 비었거나(도구·부자재 등), 같은 상품이 로트행과 로트없는 행으로 중복
    기재된 경우는 **태영 WMS 데이터 입력 문제**다. 파서 특수처리로 보정하지 않고,
    태영에 raw 데이터(현재고 파일) 정정을 요청해 해결한다 (로직은 한번 고정하고
    흔들지 않는다).
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

        total = _to_int(_get(r, "total_qty"))
        if total is None or total <= 0:
            # 현재고 ≤ 0 = 부족표시행(현재고 = -부족재고) 또는 빈 행 → 재고 아님.
            continue

        wip = _to_int(_get(r, "wip_qty")) or 0
        reserved = _to_int(_get(r, "reserved_qty")) or 0

        rows.append(WmsInventoryRow(
            barcode=barcode,
            product_name=_to_str_opt(_get(r, "product_name")),
            loc_group=_to_str_opt(_get(r, "warehouse_zone")),
            loc=_to_str_opt(_get(r, "loc")),
            total_qty=total,
            alloc_qty=wip + reserved,
            available_qty=_to_int(_get(r, "available_qty")),
            # 소비기한 컬럼만 사용. 비어 있으면 None (제조일자로 대체하지 않는다).
            expiry_short=_yyyymmdd_to_date(_get(r, "expiry")),
            expiry_long=None,
            # 제조일자는 재고 판정엔 안 쓰고, raw 데이터 품질 얼럿용으로만 보관.
            manufacture_date=_yyyymmdd_to_date(_get(r, "manufacture_date")),
            raw={str(j): (str(v) if v not in (None, "") else None) for j, v in enumerate(r)},
        ))
    return rows


#: LOC 이 이 값인 행은 가능재고에서 제외 (이미 출고 대기/피킹 완료 상태)
EXCLUDED_LOCS = {"RELEASEAREA"}


def find_missing_expiry_products(
    snapshot: WmsSnapshot,
    excluded_locs: set[str] = EXCLUDED_LOCS,
) -> list[dict[str, Any]]:
    """재고(가용>0)가 있는데 **소비기한 또는 제조일자**가 비어 있는 상품을 바코드
    단위로 요약. 태영 raw 데이터 정정 대상 얼럿용.

    재고가 있는 행(가용수량 > 0, 제외 LOC 아님)만 대상. 소비기한 또는 제조일자가
    빈 행이 하나라도 있으면 그 상품을 결과에 포함한다.

    Returns:
        [{"barcode", "product_name", "available", "missing_available",
          "missing_expiry_available",  # 소비기한 없는 행만의 가용재고
          "missing_expiry"(bool), "missing_manufacture"(bool),
          "missing_rows", "total_rows", "all_missing"}]  — 누락 가용수량 큰 순
    """
    excluded_norm = {s.strip().upper() for s in excluded_locs}
    acc: dict[str, dict[str, Any]] = {}

    for row in snapshot.rows:
        if not row.barcode:
            continue
        if row.loc and row.loc.strip().upper() in excluded_norm:
            continue
        av = row.available_qty or 0
        if av <= 0:
            continue
        a = acc.setdefault(row.barcode, {
            "barcode": row.barcode,
            "product_name": row.product_name,
            "available": 0,
            "missing_rows": 0,
            "total_rows": 0,
            "missing_available": 0,
            "missing_expiry_available": 0,   # 소비기한 없는 행만의 가용재고
            "missing_expiry": False,
            "missing_manufacture": False,
        })
        a["total_rows"] += 1
        a["available"] += av
        if not a["product_name"] and row.product_name:
            a["product_name"] = row.product_name
        miss_e = row.expiry_short is None
        miss_m = row.manufacture_date is None
        if miss_e or miss_m:
            a["missing_rows"] += 1
            a["missing_available"] += av
            if miss_e:
                a["missing_expiry"] = True
                a["missing_expiry_available"] += av
            if miss_m:
                a["missing_manufacture"] = True

    result = [a for a in acc.values() if a["missing_rows"] > 0]
    for a in result:
        a["all_missing"] = a["missing_rows"] == a["total_rows"]
    result.sort(key=lambda a: -a["missing_available"])
    return result


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
