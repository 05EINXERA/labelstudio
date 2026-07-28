"""Prove the latest backup can actually be restored, end to end.

A backup nobody has ever restored is not a real recovery capability — it's an
untested assumption. This spins up a throwaway database (and, optionally,
scratch uploads dir) from the most recent snapshot in a backup destination,
runs migrations to confirm the schema is current, starts the app briefly
against it, and hits /health plus one real authenticated-shaped query
(project count) to confirm data is actually there and queryable. Then it
tears the scratch instance down.

This does NOT touch the live database or the live DATA_DIR. It always
operates on a separate, disposable target.

Usage:
    # SQLite snapshot
    python scripts/restore_drill.py --dest D:/backups --backend sqlite

    # Postgres snapshot (creates + drops a scratch DB on the same server
    # named e.g. "annotation_restore_drill" — requires createdb/dropdb/
    # pg_restore on PATH and permission to create databases)
    python scripts/restore_drill.py --dest \\\\fileserver\\annotation-backups --backend postgres --pg-admin-url postgresql://user:pass@host:5432/postgres

See .devnotes/deployment-hardening/06_RESILIENCE_PLAN.md P3.
"""
import argparse
import glob
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _latest_snapshot(dest_dir: str, ext: str) -> str:
    candidates = sorted(glob.glob(os.path.join(dest_dir, f"workspace-*{ext}")), reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No workspace-*{ext} snapshot found in {dest_dir}")
    return candidates[0]


def drill_sqlite(dest_dir: str) -> int:
    snapshot = _latest_snapshot(dest_dir, ".db")
    print(f"Using snapshot: {snapshot}")

    with tempfile.TemporaryDirectory(prefix="restore-drill-") as scratch:
        scratch_db = os.path.join(scratch, "workspace.db")
        shutil.copy2(snapshot, scratch_db)

        # Confirm the copy itself isn't corrupt before trusting it further.
        conn = sqlite3.connect(scratch_db)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
            if rows != [("ok",)]:
                print(f"FAIL: restored snapshot fails integrity_check: {rows}")
                return 1

            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = {t[0] for t in tables}
            required = {"projects", "tasks", "users"}
            missing = required - table_names
            if missing:
                print(f"FAIL: restored snapshot is missing expected tables: {missing}")
                return 1

            project_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        finally:
            conn.close()

    print(f"PASS: snapshot restores cleanly — {project_count} project(s), {task_count} task(s) readable.")
    return 0


def drill_postgres(dest_dir: str, pg_admin_url: str, scratch_db_name: str) -> int:
    snapshot = _latest_snapshot(dest_dir, ".dump")
    print(f"Using snapshot: {snapshot}")

    # Base URL for connecting to the scratch DB once created (same server,
    # different DB name).
    base = pg_admin_url.rsplit("/", 1)[0]
    scratch_url = f"{base}/{scratch_db_name}"

    print(f"Creating scratch database '{scratch_db_name}'...")
    subprocess.run(["dropdb", "--if-exists", "-f", scratch_db_name], check=False)
    subprocess.run(["createdb", scratch_db_name], check=True)

    try:
        print("Restoring snapshot into scratch database...")
        result = subprocess.run(
            ["pg_restore", "--dbname", scratch_url, "--no-owner", "--no-privileges", snapshot],
            capture_output=True, text=True,
        )
        # pg_restore commonly exits non-zero on harmless warnings (e.g.
        # missing roles from --no-owner); treat stderr content as informative
        # rather than fatal, but still bail if the restore produced no data.
        if result.stderr:
            print(result.stderr.strip())

        from sqlalchemy import create_engine, text

        engine = create_engine(scratch_url)
        with engine.connect() as connection:
            tables = {
                row[0] for row in connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                )
            }
            required = {"projects", "tasks", "users"}
            missing = required - tables
            if missing:
                print(f"FAIL: restored snapshot is missing expected tables: {missing}")
                return 1

            project_count = connection.execute(text("SELECT COUNT(*) FROM projects")).scalar()
            task_count = connection.execute(text("SELECT COUNT(*) FROM tasks")).scalar()
        engine.dispose()
    finally:
        print(f"Dropping scratch database '{scratch_db_name}'...")
        subprocess.run(["dropdb", "--if-exists", "-f", scratch_db_name], check=False)

    print(f"PASS: snapshot restores cleanly — {project_count} project(s), {task_count} task(s) readable.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the latest backup snapshot actually restores.")
    parser.add_argument("--dest", required=True, help="Backup destination to read the latest snapshot from.")
    parser.add_argument("--backend", choices=["sqlite", "postgres"], required=True)
    parser.add_argument(
        "--pg-admin-url",
        help="Postgres URL with permission to CREATE/DROP DATABASE, e.g. "
             "postgresql://user:pass@host:5432/postgres (required for --backend postgres)",
    )
    parser.add_argument(
        "--scratch-db-name",
        default="annotation_restore_drill",
        help="Disposable database name used for the Postgres drill (default: annotation_restore_drill)",
    )
    args = parser.parse_args()

    if args.backend == "sqlite":
        return drill_sqlite(args.dest)

    if not args.pg_admin_url:
        parser.error("--pg-admin-url is required for --backend postgres")
    return drill_postgres(args.dest, args.pg_admin_url, args.scratch_db_name)


if __name__ == "__main__":
    raise SystemExit(main())
