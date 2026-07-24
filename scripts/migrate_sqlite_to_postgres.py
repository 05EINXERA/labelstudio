"""Copy an existing SQLite workspace into the configured Postgres database.

Used once when moving the LAN deployment off SQLite (hardening plan B-4b).
The target schema must already exist and be stamped at the same Alembic
revision as the source — this script moves *rows*, it does not migrate schema.

Design notes:

- Tables are copied parents-first so foreign keys are satisfied without having
  to defer constraints.
- Primary keys are preserved. Annotations reference label ids and task ids, and
  `Task.annotations` is an opaque JSON blob containing `labelId` values, so
  re-numbering rows would silently break every annotation in the database.
- Because ids are preserved, Postgres' identity sequences are left pointing at
  1 and the next insert would collide. `_resync_sequences` fixes that; skipping
  it is the classic "works until the first new project" failure.
- The whole copy runs in one transaction: a partial migration is worse than a
  failed one.
- Idempotent by refusing to run against a non-empty target rather than by
  merging, which could not be done safely with preserved ids.

Usage:
    python scripts/migrate_sqlite_to_postgres.py [--source workspace.db] [--dry-run]
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, inspect, text  # noqa: E402

import config  # noqa: E402

# Parents before children. `alembic_version` is deliberately absent: the target
# is stamped by alembic itself, and copying it would hide a version mismatch.
TABLE_ORDER = [
    "users",
    "projects",
    "tasks",
    "labels",
    "team_members",
    "workspace_data",
]

# Tables whose integer primary key comes from a sequence that must be advanced
# past the copied rows.
SEQUENCE_TABLES = {
    "users": "id",
    "projects": "id",
    "tasks": "id",
}


def _sqlite_rows(conn: sqlite3.Connection, table: str):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(f'SELECT * FROM "{table}"')]


def _common_columns(rows, target_columns):
    """Columns present in both the source rows and the target table.

    A source that predates a column (or carries one since dropped) must not
    abort the copy; the mismatch is reported instead.
    """
    if not rows:
        return []
    source_columns = list(rows[0].keys())
    return [c for c in source_columns if c in target_columns]


def _resync_sequences(connection) -> None:
    """Point each identity sequence past the highest copied id.

    Without this the sequence still starts at 1 while rows already occupy
    1..N, so the first insert after migration fails on a duplicate key.
    """
    for table, pk in SEQUENCE_TABLES.items():
        connection.execute(text(f"""
            SELECT setval(
                pg_get_serial_sequence('{table}', '{pk}'),
                COALESCE((SELECT MAX({pk}) FROM {table}), 1),
                (SELECT MAX({pk}) IS NOT NULL FROM {table})
            )
        """))


def migrate(source_path: str, dry_run: bool = False) -> int:
    if not os.path.isfile(source_path):
        print(f"ERROR: source database not found: {source_path}")
        return 1

    target_url = config.DATABASE_URL
    if target_url.startswith("sqlite"):
        print(
            "ERROR: DATABASE_URL still points at SQLite:\n"
            f"  {target_url}\n"
            "Set DATABASE_URL to the Postgres instance before migrating."
        )
        return 1

    safe_url = target_url
    if "@" in safe_url:
        head, tail = safe_url.split("@", 1)
        safe_url = head.rsplit(":", 1)[0] + ":***@" + tail
    print(f"Source: {source_path}")
    print(f"Target: {safe_url}\n")

    src = sqlite3.connect(source_path)
    engine = create_engine(target_url)
    inspector = inspect(engine)
    target_tables = set(inspector.get_table_names())

    missing = [t for t in TABLE_ORDER if t not in target_tables]
    if missing:
        print(f"ERROR: target is missing tables: {', '.join(missing)}")
        print("Run `alembic upgrade head` against the target first.")
        return 1

    # Refuse to write into a database that already holds data: ids are
    # preserved, so a second run would collide rather than merge.
    with engine.connect() as check:
        for table in TABLE_ORDER:
            count = check.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            if count:
                print(f"ERROR: target table '{table}' already has {count} rows.")
                print("Refusing to migrate into a non-empty database.")
                return 1

    plan, total = [], 0
    for table in TABLE_ORDER:
        rows = _sqlite_rows(src, table)
        target_columns = {c["name"] for c in inspector.get_columns(table)}
        columns = _common_columns(rows, target_columns)
        if rows:
            dropped = set(rows[0].keys()) - set(columns)
            if dropped:
                print(f"  note: {table} source columns not in target, skipped: {sorted(dropped)}")
        plan.append((table, rows, columns))
        total += len(rows)
        print(f"  {table:16s} {len(rows):6d} rows")

    print(f"\nTotal: {total} rows")

    if dry_run:
        print("\nDry run — nothing written.")
        return 0

    # One transaction: a half-migrated database is worse than none.
    with engine.begin() as connection:
        for table, rows, columns in plan:
            if not rows:
                continue
            col_list = ", ".join(f'"{c}"' for c in columns)
            placeholders = ", ".join(f":{c}" for c in columns)
            stmt = text(f'INSERT INTO {table} ({col_list}) VALUES ({placeholders})')
            connection.execute(stmt, [{c: r[c] for c in columns} for r in rows])
            print(f"  copied {table}: {len(rows)}")
        _resync_sequences(connection)
        print("  sequences resynced")

    # Verify against the source rather than trusting the inserts.
    print("\nVerifying...")
    ok = True
    with engine.connect() as connection:
        for table, rows, _ in plan:
            got = connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            flag = "OK " if got == len(rows) else "MISMATCH"
            if got != len(rows):
                ok = False
            print(f"  {flag} {table:16s} source={len(rows):6d} target={got:6d}")

    src.close()
    if not ok:
        print("\nVerification FAILED.")
        return 1
    print("\nMigration complete.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="workspace.db",
                        help="path to the SQLite database (default: workspace.db)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be copied without writing")
    args = parser.parse_args()
    return migrate(args.source, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
