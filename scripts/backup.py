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

BACKUP_TRUNCATION (2026-09-02)
------------------------------
Several dumps landed at ~1.7-2.9 GB against a normal ~3.8 GB and were silently
recorded as successful. They were not corrupt databases and not a script bug:
the deployment box is a laptop, and every truncated dump matches a
Kernel-Power event 105 ("power source change") to the second, with Task
Scheduler reporting 0x8007050B. Windows stops a scheduled task by default when
the machine moves onto battery, killing pg_dump mid-write.

Two defences, because the scheduler settings are on the deployed box and can
drift or be re-registered by hand:

1. schedule-backup.ps1 passes -DontStopIfGoingOnBatteries /
   -AllowStartIfOnBatteries and a 4h ExecutionTimeLimit (the old 30m limit was
   only ~2 minutes above the real 28m runtime, so growth alone would soon have
   truncated every run).
2. Here: dump to .part, verify by streaming the archive through pg_restore,
   and only then rename. A dump that never completes therefore never acquires
   a snapshot filename, never counts in prune(), and never reports ok=true.
   Note the verification must read the whole archive -- `pg_restore --list`
   exits 0 on these truncated files. See verify_postgres_dump.
"""
import argparse
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import DATABASE_URL, DATA_DIR, IS_SQLITE  # noqa: E402

STATUS_FILENAME = "last_backup_status.json"


def _write_status(dest_dir: str, ok: bool, detail: str) -> None:
    """Write a small status file next to the backup so a human (or a future
    monitoring check) can tell at a glance whether the last run succeeded,
    without having to read the Task Scheduler history. See
    06_RESILIENCE_PLAN.md P2/P6 — there is no alerting pipeline for a
    20-person deployment, so this is the low-tech baseline: open the file,
    check the date and "ok" field.
    """
    status_path = os.path.join(dest_dir, STATUS_FILENAME)
    payload = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "ok": ok,
        "detail": detail,
    }
    try:
        with open(status_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except OSError as exc:
        # Never let status-reporting itself fail the backup run.
        print(f"  could not write status file: {exc}")


def _timestamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def check_sqlite_integrity(db_path: str) -> None:
    """Raise if the source database fails PRAGMA integrity_check.

    Backing up a corrupt database over a good prior snapshot is worse than
    not backing up at all — it silently replaces the last good copy with a
    broken one. Checked before the online backup, not after, so a failure
    aborts before any snapshot is written (06_RESILIENCE_PLAN.md P4).
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    finally:
        conn.close()
    if rows != [("ok",)]:
        problems = "; ".join(row[0] for row in rows)
        raise sqlite3.DatabaseError(f"integrity_check failed: {problems}")


def backup_sqlite(dest_dir: str) -> str:
    """Consistent copy of a live SQLite database via the online backup API."""
    db_path = DATABASE_URL.replace("sqlite:///", "")
    check_sqlite_integrity(db_path)

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


def verify_postgres_dump(path: str) -> None:
    """Raise if `path` is not a readable, complete custom-format dump.

    pg_dump killed mid-write (see BACKUP_TRUNCATION above) leaves a file that
    looks entirely normal: plausible name, multi-GB size, no error on disk.

    `pg_restore --list` is NOT sufficient and was tried first: the table of
    contents sits at the head of the archive, so it lists cleanly and exits 0
    even on a dump missing half its data. Verified against the real truncated
    2026-09-02 07:11 dump -- `--list` returned 0, and its TOC tail was
    indistinguishable from a good dump's.

    Streaming the whole archive to a null output is what actually catches it:
    the truncated dump fails with "could not read from input file: end of
    file" while a complete one exits 0. This decompresses every block, so it
    costs real CPU and a few minutes on a 3.8 GB dump -- acceptable once an
    hour to know the backup is restorable, and the reason the scheduled task's
    time limit is 4h rather than 30m.

    -f os.devnull writes SQL nowhere and never connects to a server, so this
    cannot touch the live database.

    Without this check a truncated dump is recorded ok=true and counts as a
    snapshot in prune(), so it can evict a good one.
    """
    result = subprocess.run(
        ["pg_restore", "-f", os.devnull, path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, "pg_restore verify",
            stderr=result.stderr.strip() or "archive is unreadable or truncated",
        )


def backup_postgres(dest_dir: str) -> str:
    """pg_dump in custom format, restorable with pg_restore.

    Written to a .part file and renamed only after verify_postgres_dump
    confirms the archive is complete, so an interrupted run can never leave a
    file that looks like a valid snapshot. See BACKUP_TRUNCATION above.
    """
    target = os.path.join(dest_dir, f"workspace-{_timestamp()}.dump")
    partial = target + ".part"
    # psycopg-style URLs need the SQLAlchemy driver suffix stripped for pg_dump.
    url = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
    try:
        subprocess.run(
            ["pg_dump", "--format=custom", "--file", partial, url],
            check=True,
        )
        verify_postgres_dump(partial)
    except (subprocess.CalledProcessError, OSError):
        # Leave nothing behind that prune() could mistake for a real snapshot.
        try:
            os.remove(partial)
        except OSError as exc:
            print(f"  could not remove partial dump {partial}: {exc}")
        raise
    os.replace(partial, target)
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


def _dir_size(path: str) -> int:
    """Total size in bytes of all files under path (non-recursive is not enough
    for the uploads mirror, which is a flat dir, but this stays correct if that
    ever changes)."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def check_disk_space(path: str, required_bytes: int) -> None:
    """Raise if the filesystem holding `path` has less than `required_bytes`
    free. A full disk otherwise fails mid-write with a truncated, unusable
    backup instead of a clear error (06_RESILIENCE_PLAN.md P2)."""
    os.makedirs(path, exist_ok=True)
    free = shutil.disk_usage(path).free
    if free < required_bytes:
        raise OSError(
            f"Only {free / 1e9:.2f} GB free at {path}, "
            f"need at least {required_bytes / 1e9:.2f} GB — aborting to avoid a partial backup."
        )


def clean_partials(dest_dir: str) -> int:
    """Remove .part files left by a run that was killed mid-dump.

    Nothing else deletes these: the run that created one did not survive to
    reach its own cleanup. Left alone they accumulate at multiple GB each.
    """
    removed = 0
    try:
        names = os.listdir(dest_dir)
    except OSError as exc:
        print(f"  could not scan {dest_dir} for partial dumps: {exc}")
        return 0
    for name in names:
        if not (name.startswith("workspace-") and name.endswith(".part")):
            continue
        try:
            os.remove(os.path.join(dest_dir, name))
            removed += 1
        except OSError as exc:
            print(f"  could not remove {name}: {exc}")
    return removed


def prune(dest_dir: str, keep: int) -> int:
    """Delete all but the newest `keep` database backups.

    Only completed snapshots are considered. A .part file is an in-progress or
    abandoned dump and must never count towards `keep` -- otherwise a run that
    is killed mid-dump evicts a good snapshot in favour of a broken one.
    """
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


# Minimum free space required at the backup destination before starting, as a
# multiple of the current on-disk database size — a rough but effective guard
# against writing a truncated snapshot onto a nearly-full share. Floored at
# 500 MB so a tiny dev database still gets a meaningful margin.
_MIN_FREE_MULTIPLIER = 2
_MIN_FREE_FLOOR_BYTES = 500 * 1024 * 1024

# Minimum free space required on DATA_DIR itself (the live source volume).
# Uploads/logs/DB writes there fail silently or ambiguously once the disk
# fills — this is a warning, not a hard abort, since it's the *source*, not
# the backup we're about to write.
_SOURCE_MIN_FREE_BYTES = 1 * 1024 * 1024 * 1024


def _latest_snapshot_size(dest_dir: str) -> int:
    """Size of the most recent good snapshot, as a proxy for the next one.

    Used only for the Postgres space check. It is deliberately based on the
    previous *dump* rather than the database size: a custom-format dump is
    compressed, so pg_database_size would over-demand by several GB, and the
    dump-to-dump growth is small and steady.
    """
    try:
        names = [f for f in os.listdir(dest_dir)
                 if f.startswith("workspace-") and f.endswith(".dump")]
    except OSError:
        return 0
    sizes = []
    for name in names:
        try:
            sizes.append(os.path.getsize(os.path.join(dest_dir, name)))
        except OSError:
            continue
    return max(sizes) if sizes else 0


def _current_db_size(dest_dir: str) -> int:
    if IS_SQLITE:
        db_path = DATABASE_URL.replace("sqlite:///", "")
        return os.path.getsize(db_path) if os.path.isfile(db_path) else 0
    # A Postgres dump's size is not derivable from the cluster cheaply (and
    # pg_database_size measures the uncompressed heap, which is far larger
    # than the dump). The previous snapshot is the best available estimate;
    # falls back to the floor on the very first run.
    return _latest_snapshot_size(dest_dir)


def check_source_disk_space() -> None:
    """Warn (don't abort — this isn't the write we're guarding) if DATA_DIR's
    volume is running low. A full source disk is otherwise a silent cause of
    failed uploads and failed DB writes with no obvious diagnostic."""
    try:
        free = shutil.disk_usage(DATA_DIR).free
    except OSError as exc:
        print(f"  could not check source disk space at {DATA_DIR}: {exc}")
        return
    if free < _SOURCE_MIN_FREE_BYTES:
        print(
            f"WARNING: only {free / 1e9:.2f} GB free on the volume holding DATA_DIR "
            f"({DATA_DIR}) — uploads and database writes may start failing soon."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up the database and uploads.")
    parser.add_argument("--dest", required=True, help="Backup directory (ideally off this machine).")
    parser.add_argument("--keep", type=int, default=7, help="How many database snapshots to retain.")
    parser.add_argument("--skip-uploads", action="store_true")
    args = parser.parse_args()

    check_source_disk_space()

    required = max(_current_db_size(args.dest) * _MIN_FREE_MULTIPLIER, _MIN_FREE_FLOOR_BYTES)
    try:
        check_disk_space(args.dest, required)
    except OSError as exc:
        print(f"Backup destination disk-space check FAILED: {exc}")
        _write_status(args.dest, ok=False, detail=str(exc))
        return 1

    removed_partials = clean_partials(args.dest)
    if removed_partials:
        print(f"Removed {removed_partials} stale partial dump(s) from an interrupted run")

    try:
        target = backup_sqlite(args.dest) if IS_SQLITE else backup_postgres(args.dest)
    except (sqlite3.Error, subprocess.CalledProcessError, OSError) as exc:
        print(f"Database backup FAILED: {exc}")
        _write_status(args.dest, ok=False, detail=str(exc))
        return 1
    print(f"Database -> {target}")

    copied = 0
    if not args.skip_uploads:
        try:
            copied = mirror_uploads(args.dest)
            print(f"Uploads  -> {copied} new file(s)")
        except OSError as exc:
            # The database snapshot already succeeded; report and keep it.
            print(f"Upload mirror FAILED: {exc}")
            _write_status(args.dest, ok=False, detail=f"upload mirror failed: {exc}")
            return 1

    removed = prune(args.dest, args.keep)
    if removed:
        print(f"Pruned {removed} old snapshot(s), keeping {args.keep}")

    # Visibility into destination growth over time — nothing prunes the
    # upload mirror today, so at minimum its size should show up somewhere
    # a human glances at (06_RESILIENCE_PLAN.md P2).
    total_size = _dir_size(args.dest)
    print(f"Backup destination total size: {total_size / 1e9:.2f} GB")

    _write_status(args.dest, ok=True, detail=f"{target}; {copied} upload(s) copied; {total_size / 1e9:.2f} GB total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
