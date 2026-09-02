"""Tests for scripts/backup.py -- the truncated-backup guards.

Context: on 2026-09-02 several hourly dumps landed at roughly half size and
were still recorded ok=true, because a laptop power-source change let Task
Scheduler kill pg_dump mid-write. These cover the script-side defences.
See scripts/backup.py's BACKUP_TRUNCATION note.

Later the same day a *second*, unrelated cause surfaced: five full-size dumps
that exited 0 and still failed to restore, because overlapping hourly runs
shared one destination directory and one run's prune()/rename disturbed the
other's file. TestDestinationLock covers that; see BACKUP_OVERLAP.
"""
import importlib.util
import os
import subprocess
import sys
import time

import pytest

_BACKUP_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "backup.py"
)


def _load_backup_module():
    """Load backup.py by path: scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("backup_script", _BACKUP_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup = _load_backup_module()


class TestCleanPartials:
    def test_removes_partial_dumps(self, tmp_path):
        (tmp_path / "workspace-20260902-090000.dump.part").write_bytes(b"half a dump")
        assert backup.clean_partials(str(tmp_path)) == 1
        assert not (tmp_path / "workspace-20260902-090000.dump.part").exists()

    def test_leaves_completed_snapshots_alone(self, tmp_path):
        keep = tmp_path / "workspace-20260902-090000.dump"
        keep.write_bytes(b"a complete dump")
        assert backup.clean_partials(str(tmp_path)) == 0
        assert keep.exists()

    def test_ignores_unrelated_part_files(self, tmp_path):
        other = tmp_path / "something-else.part"
        other.write_bytes(b"not ours")
        assert backup.clean_partials(str(tmp_path)) == 0
        assert other.exists()


class TestPrune:
    def test_partials_never_count_towards_keep(self, tmp_path):
        """A killed run must not evict a good snapshot.

        With keep=2 and two real snapshots, a leftover .part must not push the
        oldest good dump out of the retention window.
        """
        for name in ("workspace-20260901-080000.dump", "workspace-20260901-090000.dump"):
            (tmp_path / name).write_bytes(b"real")
        (tmp_path / "workspace-20260901-100000.dump.part").write_bytes(b"partial")

        assert backup.prune(str(tmp_path), keep=2) == 0
        assert (tmp_path / "workspace-20260901-080000.dump").exists()
        assert (tmp_path / "workspace-20260901-090000.dump").exists()

    def test_removes_oldest_beyond_keep(self, tmp_path):
        for name in (
            "workspace-20260901-080000.dump",
            "workspace-20260901-090000.dump",
            "workspace-20260901-100000.dump",
        ):
            (tmp_path / name).write_bytes(b"real")

        assert backup.prune(str(tmp_path), keep=2) == 1
        assert not (tmp_path / "workspace-20260901-080000.dump").exists()
        assert (tmp_path / "workspace-20260901-100000.dump").exists()


class TestLatestSnapshotSize:
    def test_uses_largest_snapshot(self, tmp_path):
        (tmp_path / "workspace-20260901-080000.dump").write_bytes(b"x" * 100)
        (tmp_path / "workspace-20260901-090000.dump").write_bytes(b"x" * 500)
        assert backup._latest_snapshot_size(str(tmp_path)) == 500

    def test_ignores_partials(self, tmp_path):
        """A truncated .part must not become the size estimate -- that would
        under-estimate the space needed for the next real dump."""
        (tmp_path / "workspace-20260901-080000.dump").write_bytes(b"x" * 100)
        (tmp_path / "workspace-20260901-090000.dump.part").write_bytes(b"x" * 900)
        assert backup._latest_snapshot_size(str(tmp_path)) == 100

    def test_zero_on_first_run(self, tmp_path):
        assert backup._latest_snapshot_size(str(tmp_path)) == 0


@pytest.mark.skipif(
    __import__("shutil").which("pg_restore") is None, reason="pg_restore not on PATH"
)
class TestVerifyPostgresDump:
    def test_rejects_a_truncated_archive(self, tmp_path):
        """The regression that started this: a half-written dump must not pass.

        Note this is why verification streams the archive rather than calling
        `pg_restore --list` -- the real truncated dump listed cleanly and
        exited 0, because the table of contents sits at the head of the file.
        """
        bad = tmp_path / "workspace-20260902-071122.dump"
        # A custom-format header ("PGDMP") followed by nothing readable, which
        # is the shape of a dump killed mid-write.
        bad.write_bytes(b"PGDMP" + b"\x00" * 512)
        with pytest.raises(subprocess.CalledProcessError):
            backup.verify_postgres_dump(str(bad))

    def test_rejects_a_non_archive(self, tmp_path):
        junk = tmp_path / "workspace-20260902-090000.dump"
        junk.write_bytes(b"not a dump at all")
        with pytest.raises(subprocess.CalledProcessError):
            backup.verify_postgres_dump(str(junk))


class TestDestinationLock:
    """The 2026-09-02 part-2 regression: two runs sharing one destination
    corrupted each other's dumps. See backup.py's BACKUP_OVERLAP note.
    """

    def test_second_run_is_refused_while_the_first_holds_the_lock(self, tmp_path):
        with backup.destination_lock(str(tmp_path)):
            with pytest.raises(backup.BackupInProgress):
                with backup.destination_lock(str(tmp_path)):
                    pass

    def test_lock_is_released_on_exit(self, tmp_path):
        with backup.destination_lock(str(tmp_path)):
            pass
        # A later run must be able to take it again.
        with backup.destination_lock(str(tmp_path)):
            pass
        assert not (tmp_path / backup.LOCK_FILENAME).exists()

    def test_lock_is_released_when_the_run_raises(self, tmp_path):
        with pytest.raises(ValueError):
            with backup.destination_lock(str(tmp_path)):
                raise ValueError("dump failed")
        assert not (tmp_path / backup.LOCK_FILENAME).exists()

    def test_records_pid_and_start_time(self, tmp_path):
        with backup.destination_lock(str(tmp_path)):
            held = backup._read_lock(str(tmp_path / backup.LOCK_FILENAME))
        assert held["pid"] == os.getpid()
        assert held["started"]

    def test_stale_lock_is_broken(self, tmp_path):
        """A hard-killed run never reaches its own cleanup. Its lock must not
        block backups forever."""
        lock = tmp_path / backup.LOCK_FILENAME
        lock.write_text('{"pid": 999999, "started": "2026-09-02T07:09:36"}', encoding="utf-8")
        stale = time.time() - (backup._LOCK_STALE_SECONDS + 60)
        os.utime(lock, (stale, stale))

        with backup.destination_lock(str(tmp_path)):
            held = backup._read_lock(str(lock))
        assert held["pid"] == os.getpid()

    def test_fresh_lock_is_not_broken(self, tmp_path):
        lock = tmp_path / backup.LOCK_FILENAME
        lock.write_text('{"pid": 999999, "started": "2026-09-02T16:00:00"}', encoding="utf-8")
        recent = time.time() - (backup._LOCK_STALE_SECONDS - 600)
        os.utime(lock, (recent, recent))

        with pytest.raises(backup.BackupInProgress):
            with backup.destination_lock(str(tmp_path)):
                pass

    def test_malformed_lock_is_still_honoured_while_fresh(self, tmp_path):
        """An unreadable lock must not be treated as absent -- a live run may
        have been interrupted between creating the file and writing it."""
        (tmp_path / backup.LOCK_FILENAME).write_text("{ not json", encoding="utf-8")
        with pytest.raises(backup.BackupInProgress):
            with backup.destination_lock(str(tmp_path)):
                pass

    def test_skipped_run_exits_zero_and_leaves_status_alone(self, tmp_path, monkeypatch):
        """A refused run is not a backup failure: the scheduler simply fired
        while the previous run was going. It must exit 0 (a non-zero code
        raises a Task Scheduler alert for a non-problem) and must not overwrite
        the status file, which still describes the last real run.
        """
        status = tmp_path / backup.STATUS_FILENAME
        status.write_text('{"ok": true, "detail": "previous good run"}', encoding="utf-8")

        monkeypatch.setattr(
            sys, "argv", ["backup.py", "--dest", str(tmp_path), "--skip-uploads"]
        )

        def _must_not_run(_args):
            raise AssertionError("_run_backup must not execute while the lock is held")

        monkeypatch.setattr(backup, "_run_backup", _must_not_run)

        with backup.destination_lock(str(tmp_path)):
            assert backup.main() == 0

        assert '"ok": true' in status.read_text(encoding="utf-8")
        assert "previous good run" in status.read_text(encoding="utf-8")

    def test_lock_is_held_across_the_whole_run(self, tmp_path, monkeypatch):
        """The corruption happened during prune()/rename, not only the dump,
        so the lock must cover the entire run rather than just pg_dump."""
        monkeypatch.setattr(
            sys, "argv", ["backup.py", "--dest", str(tmp_path), "--skip-uploads"]
        )
        seen = {}

        def _fake_run(_args):
            seen["locked"] = (tmp_path / backup.LOCK_FILENAME).exists()
            return 0

        monkeypatch.setattr(backup, "_run_backup", _fake_run)

        assert backup.main() == 0
        assert seen["locked"] is True
        # ...and released afterwards.
        assert not (tmp_path / backup.LOCK_FILENAME).exists()
