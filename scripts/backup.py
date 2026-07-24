"""Back up the database and the uploaded images.

One laptop holds every annotator's work. Without a scheduled copy off the box,
a disk failure or a mistaken project delete loses all of it — annotations are
hard-deleted, there is no trash.

    python scripts/backup.py --dest \\\\fileserver\\annotation-backups
    python scripts/backup.py --dest D:/backups --keep 14

Handles both backends:
- SQLite: uses the online backup API, which is safe to run against a live
  database. A plain file copy of a WAL-mode database can capture a torn state.
- Postgres: shells out to pg_dump, which must be on PATH.

Uploads are mirrored incrementally (only new/changed files) because re-copying
tens of GB of images nightly is not viable.
"""
import argparse
import datetime
import os
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import DATABASE_URL, DATA_DIR, IS_SQLITE  # noqa: E402


def _timestamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_sqlite(dest_dir: str) -> str:
    """Consistent copy of a live SQLite database via the online backup API."""
    db_path = DATABASE_URL.replace("sqlite:///", "")
    target = os.path.join(dest_dir, f"workspace-{_timestamp()}.db")

    source = sqlite3.connect(db_path)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return target


def backup_postgres(dest_dir: str) -> str:
    """pg_dump in custom format, restorable with pg_restore."""
    target = os.path.join(dest_dir, f"workspace-{_timestamp()}.dump")
    # psycopg-style URLs need the SQLAlchemy driver suffix stripped for pg_dump.
    url = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
    subprocess.run(
        ["pg_dump", "--format=custom", "--file", target, url],
        check=True,
    )
    return target


def mirror_uploads(dest_dir: str) -> int:
    """Copy uploads that are new or changed. Returns the number copied."""
    source_dir = os.path.join(DATA_DIR, "uploads")
    if not os.path.isdir(source_dir):
        return 0

    target_dir = os.path.join(dest_dir, "uploads")
    os.makedirs(target_dir, exist_ok=True)

    copied = 0
    for name in os.listdir(source_dir):
        source = os.path.join(source_dir, name)
        if not os.path.isfile(source):
            continue
        target = os.path.join(target_dir, name)
        # Uploads are content-addressed by a uuid filename and never rewritten,
        # so presence plus matching size is a sufficient freshness check.
        if os.path.exists(target) and os.path.getsize(target) == os.path.getsize(source):
            continue
        shutil.copy2(source, target)
        copied += 1
    return copied


def prune(dest_dir: str, keep: int) -> int:
    """Delete all but the newest `keep` database backups."""
    snapshots = sorted(
        (f for f in os.listdir(dest_dir)
         if f.startswith("workspace-") and f.endswith((".db", ".dump"))),
        reverse=True,
    )
    removed = 0
    for stale in snapshots[keep:]:
        try:
            os.remove(os.path.join(dest_dir, stale))
            removed += 1
        except OSError as exc:
            print(f"  could not remove {stale}: {exc}")
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up the database and uploads.")
    parser.add_argument("--dest", required=True, help="Backup directory (ideally off this machine).")
    parser.add_argument("--keep", type=int, default=7, help="How many database snapshots to retain.")
    parser.add_argument("--skip-uploads", action="store_true")
    args = parser.parse_args()

    os.makedirs(args.dest, exist_ok=True)

    try:
        target = backup_sqlite(args.dest) if IS_SQLITE else backup_postgres(args.dest)
    except (sqlite3.Error, subprocess.CalledProcessError, OSError) as exc:
        print(f"Database backup FAILED: {exc}")
        return 1
    print(f"Database -> {target}")

    if not args.skip_uploads:
        try:
            copied = mirror_uploads(args.dest)
            print(f"Uploads  -> {copied} new file(s)")
        except OSError as exc:
            # The database snapshot already succeeded; report and keep it.
            print(f"Upload mirror FAILED: {exc}")
            return 1

    removed = prune(args.dest, args.keep)
    if removed:
        print(f"Pruned {removed} old snapshot(s), keeping {args.keep}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
