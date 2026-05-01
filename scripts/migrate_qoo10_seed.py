"""
Qoo10 시드/이력 데이터 이전: 자매 프로젝트(kat-kse-3pl-japan) DB → 신규 DB.

사용법:
    SOURCE_DATABASE_URL=postgresql://...     # 기존 DB
    TARGET_DATABASE_URL=postgresql://...     # 신규 DB
    python scripts/migrate_qoo10_seed.py [--include-history] [--dry-run]

옵션:
    --include-history : qoo10_outbound, qoo10_pending_brief 도 이전 (기본은 mapping/credentials만)
    --dry-run         : 실제 INSERT 없이 건수만 출력

각 테이블은 ON CONFLICT DO NOTHING — 신규 DB에 이미 있는 행은 건드리지 않음.
재실행 안전.
"""
import argparse
import os
import sys

import psycopg


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"환경변수 {name} 가 비어있습니다.")
    return v


def connect(dsn: str, autocommit: bool = False):
    # Supabase Transaction pooler 호환
    return psycopg.connect(dsn, prepare_threshold=None, autocommit=autocommit)


def copy_table(src, dst, table: str, columns: list[str], pk: list[str], dry_run: bool) -> tuple[int, int]:
    """ON CONFLICT (pk...) DO NOTHING upsert. (전체 source 행수, target 신규 삽입수) 반환."""
    col_csv = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    pk_csv = ", ".join(pk)
    sql_insert = (
        f"INSERT INTO {table} ({col_csv}) VALUES ({placeholders}) "
        f"ON CONFLICT ({pk_csv}) DO NOTHING"
    )

    with src.cursor() as cur_s:
        cur_s.execute(f"SELECT {col_csv} FROM {table}")
        rows = cur_s.fetchall()

    src_count = len(rows)
    if dry_run or src_count == 0:
        return src_count, 0

    inserted = 0
    with dst.cursor() as cur_d:
        for row in rows:
            cur_d.execute(sql_insert, row)
            inserted += cur_d.rowcount
    dst.commit()
    return src_count, inserted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-history", action="store_true",
                    help="qoo10_outbound + qoo10_pending_brief 도 이전")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src_dsn = env("SOURCE_DATABASE_URL")
    dst_dsn = env("TARGET_DATABASE_URL")

    if src_dsn == dst_dsn:
        sys.exit("SOURCE/TARGET DSN이 동일합니다. 다른 DB여야 합니다.")

    print(f"[migrate] SOURCE = {src_dsn.split('@')[-1]}")
    print(f"[migrate] TARGET = {dst_dsn.split('@')[-1]}")
    if args.dry_run:
        print("[migrate] DRY-RUN — INSERT 미실행")

    plan = [
        # (table, columns, pk)
        ("qoo10_credentials",
         ["id", "api_key", "user_id", "password", "expires_at", "updated_at"],
         ["id"]),
        ("qoo10_product_mapping",
         ["qoo10_name", "qoo10_option", "item_codes", "sku_codes",
          "quantities", "enabled", "updated_at"],
         ["qoo10_name", "qoo10_option"]),
    ]
    if args.include_history:
        plan.append(
            ("qoo10_pending_brief",
             ["id", "file_name", "content", "cart_count", "disabled_count",
              "created_at", "consumed_at"],
             ["id"]),
        )
        plan.append(
            ("qoo10_outbound",
             ["qoo10_cart_no", "qoo10_order_no", "sku_code", "sku_name", "planned_qty",
              "recipient", "recipient_phone", "postal_code", "address",
              "qoo10_product_name", "qoo10_option", "qoo10_qty",
              "source_file", "waybill", "waybill_updated_at", "generated_at"],
             ["qoo10_cart_no", "qoo10_order_no", "sku_code"]),
        )

    src = connect(src_dsn, autocommit=True)
    dst = connect(dst_dsn, autocommit=False)

    try:
        for table, cols, pk in plan:
            try:
                src_n, ins_n = copy_table(src, dst, table, cols, pk, args.dry_run)
                print(f"  {table:30s}  source={src_n:>6}  inserted={ins_n:>6}")
            except psycopg.errors.UndefinedTable as e:
                print(f"  {table:30s}  SKIP (소스에 없음): {e}")
                # rollback any half-baked txn
                if not dst.autocommit:
                    dst.rollback()
            except Exception as e:
                print(f"  {table:30s}  ERROR: {e}")
                if not dst.autocommit:
                    dst.rollback()

        # qoo10_pending_brief sequence 보정 (id 직접 삽입 시 다음 SERIAL이 충돌하지 않게)
        if args.include_history and not args.dry_run:
            with dst.cursor() as cur:
                cur.execute("""
                    SELECT setval(pg_get_serial_sequence('qoo10_pending_brief', 'id'),
                                  COALESCE((SELECT MAX(id) FROM qoo10_pending_brief), 1),
                                  true)
                """)
            dst.commit()
            print("  qoo10_pending_brief sequence 보정 완료")

    finally:
        src.close()
        dst.close()

    print("[migrate] 완료.")


if __name__ == "__main__":
    main()
