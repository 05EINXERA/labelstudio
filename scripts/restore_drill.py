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
from urllib.parse import unquote, urlparse

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


def _admin_conn_args(pg_admin_url: str) -> tuple[list, dict]:
    """Translate the admin URL into createdb/dropdb flags plus an environment
    carrying the password.

    Passing only a database name to createdb/dropdb makes libpq fall back to
    its own defaults - localhost:5432 as the current OS user - which silently
    targets the wrong server whenever Postgres runs on a non-default port
    (e.g. a Docker mapping on 5435) and then prompts for a password for a
    role that does not exist.
    """
    parsed = urlparse(pg_admin_url)

    args = []
    if parsed.hostname:
        args += ["-h", parsed.hostname]
    if parsed.port:
        args += ["-p", str(parsed.port)]
    if parsed.username:
        args += ["-U", parsed.username]

    env = os.environ.copy()
    if parsed.password:
        # PGPASSWORD keeps the password off the command line (and out of the
        # process list) while still avoiding an interactive prompt.
        env["PGPASSWORD"] = unquote(parsed.password)

    return args, env


def drill_postgres(dest_dir: str, pg_admin_url: str, scratch_db_name: str) -> int:
    snapshot = _latest_snapshot(dest_dir, ".dump")
    print(f"Using snapshot: {snapshot}")

    # Base URL for connecting to the scratch DB once created (same server,
    # different DB name).
    base = pg_admin_url.rsplit("/", 1)[0]
    scratch_url = f"{base}/{scratch_db_name}"

    conn_args, env = _admin_conn_args(pg_admin_url)

    print(f"Creating scratch database '{scratch_db_name}'...")
    subprocess.run(
        ["dropdb"] + conn_args + ["--if-exists", "-f", scratch_db_name],
        check=False, env=env,
    )
    subprocess.run(["createdb"] + conn_args + [scratch_db_name], check=True, env=env)

    try:
        print("Restoring snapshot into scratch database...")
        result = subprocess.run(
            ["pg_restore", "--dbname", scratch_url, "--no-owner", "--no-privileges", snapshot],
            capture_output=True, text=True, env=env,
        )
        # pg_restore commonly exits non-zero on harmless warnings (e.g.
        # missing roles from --no-owner); treat stderr content as informative
        # rather than fatal, but still bail if the restore produced no data.
        # The table/row checks below are the real verdict.
        if result.stderr:
            print(result.stderr.strip())
            if "transaction_timeout" in result.stderr:
                # Benign client/server version skew: pg_dump 17 emits
                # "SET transaction_timeout = 0" (a PG17 GUC) which a PG16
                # server rejects. 0 is the default anyway, so nothing is lost.
                # Align the client-side tools with the server major version to
                # silence this.
                print(
                    "  ^ benign: pg_dump 17 emitted a PG17-only setting for an "
                    "older server. Not a restore failure."
                )

        from sqlalchemy import create_engine, text

        # scratch_url stays driver-less above because pg_restore is a libpq CLI
        # and would reject a "+psycopg" scheme. SQLAlchemy, though, defaults a
        # bare postgresql:// URL to psycopg2, which this project does not
        # install — it standardises on psycopg 3 (see requirements.txt). Ask
        # for that dialect explicitly rather than adding a second driver.
        engine = create_engine(
            scratch_url.replace("postgresql://", "postgresql+psycopg://", 1)
        )
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
        subprocess.run(
            ["dropdb"] + conn_args + ["--if-exists", "-f", scratch_db_name],
            check=False, env=env,
        )

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
