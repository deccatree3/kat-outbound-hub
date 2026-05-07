"""nn-rocketgrowth_inventory Supabase -> kat-outbound-hub 메인 Supabase 데이터 이전.

자매2 의 11 테이블 데이터를 메인 Supabase(`tledxurnnuvmqvctuedd`) 로 복사.
스키마는 alembic 으로 미리 생성되어 있어야 함 (`alembic upgrade head`).

테이블 의존 순서:
  1) wms_product, coupang_product (independent)
  2) coupang_inventory_snapshot, wms_inventory_snapshot (independent)
  3) coupang_inventory_item, wms_inventory_item (FK to snapshots + products)
  4) inbound_plan (independent)
  5) inbound_plan_item (FK to inbound_plan)
  6) coupang_result_log, plan_file (FK to inbound_plan)
  7) activity_log (independent)

사용법:
  python scripts/migrate_rocketgrowth_data.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb


SOURCE_DSN = (
    "postgresql://postgres.cukcvoznazkyfojpviev:Srjsehddl83"
    "@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres"
)


def _target_dsn() -> str:
    """현 프로젝트의 config.json 에서 메인 Supabase DSN 가져오기."""
    repo_root = Path(__file__).resolve().parent.parent
    cfg = json.loads((repo_root / "config.json").read_text(encoding="utf-8"))
    return cfg["database_url"]


# 의존 순서대로 (자식 -> 부모 거꾸로 정렬한 것)
TABLES_IN_ORDER = [
    # 부모 (independent)
    "wms_product",
    "coupang_product",
    "coupang_inventory_snapshot",
    "wms_inventory_snapshot",
    "inbound_plan",
    "activity_log",
    # 자식
    "coupang_inventory_item",
    "wms_inventory_item",
    "inbound_plan_item",
    "coupang_result_log",
    "plan_file",
]


def _get_columns(conn, table: str) -> list[str]:
    """information_schema 에서 column 명 (ordinal_position 순) 조회."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        return [r[0] for r in cur.fetchall()]


def _count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return int(cur.fetchone()[0])


def _max_id(conn, table: str) -> int | None:
    """SERIAL pk 'id' 의 max - sequence 동기화에 사용."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s AND column_name='id'
            """,
            (table,),
        )
        if not cur.fetchone():
            return None
        cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
        return int(cur.fetchone()[0])


def _copy_table(src, dst, table: str, dry_run: bool, batch: int = 500) -> tuple[int, int]:
    """src.table -> dst.table 데이터 복사. (source_count, copied_count) 반환."""
    src_cols = _get_columns(src, table)
    dst_cols = _get_columns(dst, table)
    common = [c for c in src_cols if c in dst_cols]
    if not common:
        print(f"  [!]  {table}: 공통 컬럼 0 - 건너뜀")
        return 0, 0

    src_n = _count(src, table)
    dst_n_before = _count(dst, table)
    if dst_n_before > 0:
        print(f"  [!]  {table}: 대상 DB 에 이미 {dst_n_before} 행 - 건너뜀")
        return src_n, 0

    if dry_run:
        print(f"  [dry-run] {table}: source {src_n} 행, target 0 행 -> {src_n} 행 INSERT 예정")
        return src_n, src_n

    cols_sql = ", ".join(f'"{c}"' for c in common)
    placeholders = ", ".join(["%s"] * len(common))
    insert_sql = f'INSERT INTO {table} ({cols_sql}) VALUES ({placeholders})'

    copied = 0
    with src.cursor() as src_cur, dst.cursor() as dst_cur:
        src_cur.execute(f"SELECT {cols_sql} FROM {table}")
        while True:
            rows = src_cur.fetchmany(batch)
            if not rows:
                break
            for r in rows:
                # dict/list 는 JSONB 로 래핑
                wrapped = tuple(
                    Jsonb(v) if isinstance(v, (dict, list)) else v for v in r
                )
                dst_cur.execute(insert_sql, wrapped)
            copied += len(rows)
            print(f"    ... {copied}/{src_n}")
    dst.commit()

    # SERIAL sequence 보정
    max_id = _max_id(dst, table)
    if max_id is not None and max_id > 0:
        with dst.cursor() as dst_cur:
            dst_cur.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), %s, true)",
                (max_id,),
            )
        dst.commit()

    return src_n, copied


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="실제 INSERT 안 하고 건수만 출력")
    args = ap.parse_args()

    print(f"Source: nn-rocketgrowth_inventory Supabase ({SOURCE_DSN[:60]}...)")
    target_dsn = _target_dsn()
    print(f"Target: kat-outbound-hub 메인 Supabase ({target_dsn[:60]}...)")
    print()

    src = psycopg.connect(SOURCE_DSN, prepare_threshold=None)
    dst = psycopg.connect(target_dsn, prepare_threshold=None)

    try:
        total_src = 0
        total_copied = 0
        for tbl in TABLES_IN_ORDER:
            print(f"[T] {tbl}")
            src_n, copied = _copy_table(src, dst, tbl, args.dry_run)
            total_src += src_n
            total_copied += copied
            print(f"  -> {copied}/{src_n} 건 복사")

        print()
        print(f"=== 합계: source {total_src} -> target {total_copied} ===")

        # 검증
        print()
        print("검증 (대상 DB row count):")
        for tbl in TABLES_IN_ORDER:
            n = _count(dst, tbl)
            print(f"  {tbl:35s}  {n:>6}")

    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    main()
