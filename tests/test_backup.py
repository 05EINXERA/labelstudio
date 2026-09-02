"""Tests for scripts/backup.py -- the truncated-backup guards.

Context: on 2026-09-02 several hourly dumps landed at roughly half size and
were still recorded ok=true, because a laptop power-source change let Task
Scheduler kill pg_dump mid-write. These cover the script-side defences.
See scripts/backup.py's BACKUP_TRUNCATION note.
"""
import importlib.util
import os
import subprocess

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
