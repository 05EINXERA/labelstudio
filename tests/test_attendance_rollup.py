"""R8 — the daily rollup and the retention prune.

Two properties here are worth more than the rest, because getting either wrong
destroys data *silently* and the loss is only discovered when someone asks
about a month that is now empty:

1. **Roll up, then prune** — and a failed rollup prunes nothing at all.
2. **A re-roll equals a fresh recomputation.** If the rollup and the raw rows
   can diverge, the divergence is invisible until the raw rows are pruned, at
   which point it is unrecoverable.

The third is structural: the prune is the *only* DELETE against
`attendance_observations` anywhere in the codebase.
"""
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

import config
import models
from api import attendance_report as report
from database import SessionLocal
from scripts import rollup_attendance


LOCAL_DAY = date(2026, 9, 20)


@pytest.fixture
def user_id(client):
    import uuid
    username = f"rollup-{uuid.uuid4().hex[:10]}"
    res = client.post(
        "/api/auth/register", json={"username": username, "password": "pw-12345"}
    )
    assert res.status_code == 200
    headers = {"Authorization": f"Bearer {res.json()['access_token']}"}
    client.cookies.clear()
    return client.get("/api/auth/me", headers=headers).json()["id"]


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def _at(hour, minute, day=LOCAL_DAY):
    """A UTC instant from a Kathmandu wall time."""
    return datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=report.site_tz()
    ).astimezone(timezone.utc)


def _observe(db, user_id, hour, minute, kind="seen", task_id=None, day=LOCAL_DAY):
    row = models.AttendanceObservation(
        user_id=user_id,
        seen_at=_at(hour, minute, day),
        kind=kind,
        task_id=task_id,
        instance_id=config.ATTENDANCE_INSTANCE_ID,
    )
    db.add(row)
    return row


def _seed_ordinary_day(db, user_id, day=LOCAL_DAY):
    """09:00 to 17:00 with a 30-minute declared break, ending in a logout."""
    _observe(db, user_id, 9, 0, kind="login", day=day)
    for minute in range(0, 60, 5):
        _observe(db, user_id, 9, minute, day=day)
    for minute in range(0, 60, 5):
        _observe(db, user_id, 10, minute, day=day)
    _observe(db, user_id, 11, 0, kind="break_start", day=day)
    _observe(db, user_id, 11, 30, kind="break_end", day=day)
    for minute in range(30, 60, 5):
        _observe(db, user_id, 11, minute, day=day)
    _observe(db, user_id, 12, 0, kind="logout", day=day)
    db.commit()


def _day_row(db, user_id, day=LOCAL_DAY):
    return (
        db.query(models.AttendanceDay)
        .filter(
            models.AttendanceDay.user_id == user_id,
            models.AttendanceDay.local_date == day,
        )
        .first()
    )


def _cleanup(db, user_id):
    db.query(models.AttendanceObservation).filter(
        models.AttendanceObservation.user_id == user_id
    ).delete(synchronize_session=False)
    db.query(models.AttendanceDay).filter(
        models.AttendanceDay.user_id == user_id
    ).delete(synchronize_session=False)
    db.commit()


# --- The rollup -------------------------------------------------------------


def test_rollup_writes_a_day_row(db, user_id):
    try:
        _seed_ordinary_day(db, user_id)
        assert rollup_attendance.rollup_day(db, LOCAL_DAY) >= 1

        row = _day_row(db, user_id)
        assert row is not None
        assert row.local_date == LOCAL_DAY
        assert row.end_reason == "logout"
        assert row.session_count >= 1
        assert row.break_seconds == 30 * 60
        # Present excludes the declared break: 09:00-12:00 is 3h, minus 30m.
        assert row.present_seconds == (3 * 3600) - (30 * 60)
        # tasks_touched needs real task rows (the FK is real), so it is
        # covered by the pure sessionisation tests rather than here.
    finally:
        _cleanup(db, user_id)


def test_rollup_stores_the_username_snapshot(db, user_id):
    """Q30: a kept row must stay READABLE after the account is gone. A null
    user_id with no name is storage without evidence."""
    try:
        _seed_ordinary_day(db, user_id)
        rollup_attendance.rollup_day(db, LOCAL_DAY)
        row = _day_row(db, user_id)
        expected = db.get(models.User, user_id).username
        assert row.username == expected
    finally:
        _cleanup(db, user_id)


def test_rollup_is_idempotent(db, user_id):
    """Running twice must not duplicate or double-count — uq_attendance_day
    is what makes the re-roll safe."""
    try:
        _seed_ordinary_day(db, user_id)
        rollup_attendance.rollup_day(db, LOCAL_DAY)
        first = _day_row(db, user_id)
        first_present = first.present_seconds

        rollup_attendance.rollup_day(db, LOCAL_DAY)
        db.expire_all()

        rows = (
            db.query(models.AttendanceDay)
            .filter(
                models.AttendanceDay.user_id == user_id,
                models.AttendanceDay.local_date == LOCAL_DAY,
            )
            .all()
        )
        assert len(rows) == 1, "a second run created a duplicate day row"
        assert rows[0].present_seconds == first_present, "the re-roll accumulated"
    finally:
        _cleanup(db, user_id)


def test_a_reroll_equals_a_fresh_recomputation(db, user_id):
    """THE consistency test.

    If the stored rollup and a recomputation from raw rows can disagree, the
    divergence is invisible until the raw rows prune — and then unrecoverable.
    This is what R10's backdated break will depend on.
    """
    try:
        _seed_ordinary_day(db, user_id)
        rollup_attendance.rollup_day(db, LOCAL_DAY)

        # A break entered after the fact, exactly as R10 will write it.
        _observe(db, user_id, 10, 10, kind="break_manual_start")
        _observe(db, user_id, 10, 25, kind="break_manual_end")
        db.commit()

        # Re-roll, then recompute independently and compare.
        rollup_attendance.rollup_day(db, LOCAL_DAY)
        db.expire_all()
        stored = _day_row(db, user_id)

        start, end = report.day_bounds(LOCAL_DAY)
        raw = report.observation_dicts(
            db.query(models.AttendanceObservation)
            .filter(
                models.AttendanceObservation.user_id == user_id,
                models.AttendanceObservation.seen_at >= start,
                models.AttendanceObservation.seen_at < end,
            )
            .order_by(models.AttendanceObservation.seen_at)
            .all()
        )
        fresh = report.summarise_day(raw, LOCAL_DAY)

        for field in (
            "present_seconds", "break_seconds", "manual_break_seconds",
            "active_seconds", "session_count", "tasks_touched",
        ):
            assert getattr(stored, field) == fresh[field], (
                f"{field} diverged: rollup={getattr(stored, field)} "
                f"fresh={fresh[field]}"
            )
        assert stored.manual_break_seconds == 15 * 60
    finally:
        _cleanup(db, user_id)


def test_rollup_buckets_by_the_local_day_not_the_utc_one(db, user_id):
    """An arrival at 00:15 Kathmandu is 18:30 UTC the previous day."""
    try:
        _observe(db, user_id, 0, 15)
        _observe(db, user_id, 0, 20)
        db.commit()

        stored_utc_date = (
            db.query(models.AttendanceObservation.seen_at)
            .filter(models.AttendanceObservation.user_id == user_id)
            .first()[0]
        )
        assert report.as_utc(stored_utc_date).date() == LOCAL_DAY - timedelta(days=1)

        rollup_attendance.rollup_day(db, LOCAL_DAY)
        assert _day_row(db, user_id) is not None, (
            "the row was filed on the UTC day rather than the local one"
        )
    finally:
        _cleanup(db, user_id)


def test_a_day_with_no_observations_writes_nothing(db):
    assert rollup_attendance.rollup_day(db, date(2001, 1, 1)) == 0


def test_dry_run_writes_nothing(db, user_id):
    try:
        _seed_ordinary_day(db, user_id)
        assert rollup_attendance.rollup_day(db, LOCAL_DAY, dry_run=True) >= 1
        assert _day_row(db, user_id) is None
    finally:
        _cleanup(db, user_id)


# --- The prune --------------------------------------------------------------


def test_prune_deletes_only_rows_older_than_the_cutoff(db, user_id):
    try:
        old_day = LOCAL_DAY - timedelta(days=40)
        _observe(db, user_id, 9, 0, day=old_day)
        _observe(db, user_id, 9, 5, day=old_day)
        _observe(db, user_id, 9, 0, day=LOCAL_DAY)
        db.commit()

        deleted = rollup_attendance.prune(db, LOCAL_DAY - timedelta(days=31))
        assert deleted == 2

        remaining = (
            db.query(models.AttendanceObservation)
            .filter(models.AttendanceObservation.user_id == user_id)
            .all()
        )
        assert len(remaining) == 1
        assert report.local_day(remaining[0].seen_at) == LOCAL_DAY
    finally:
        _cleanup(db, user_id)


def test_prune_dry_run_deletes_nothing(db, user_id):
    try:
        old_day = LOCAL_DAY - timedelta(days=40)
        _observe(db, user_id, 9, 0, day=old_day)
        db.commit()

        assert rollup_attendance.prune(
            db, LOCAL_DAY - timedelta(days=31), dry_run=True
        ) == 1
        assert (
            db.query(models.AttendanceObservation)
            .filter(models.AttendanceObservation.user_id == user_id)
            .count()
        ) == 1
    finally:
        _cleanup(db, user_id)


def test_the_rolled_up_day_survives_the_prune(db, user_id):
    """The point of the ordering: raw rows go, the permanent record stays."""
    try:
        old_day = LOCAL_DAY - timedelta(days=40)
        _seed_ordinary_day(db, user_id, day=old_day)
        rollup_attendance.rollup_day(db, old_day)

        rollup_attendance.prune(db, LOCAL_DAY - timedelta(days=31))
        db.expire_all()

        assert (
            db.query(models.AttendanceObservation)
            .filter(models.AttendanceObservation.user_id == user_id)
            .count()
        ) == 0, "raw rows should be gone"
        row = _day_row(db, user_id, day=old_day)
        assert row is not None, "the permanent record was destroyed with the raw rows"
        assert row.present_seconds > 0
        assert row.username
    finally:
        _cleanup(db, user_id)


# --- The ordering interlock -------------------------------------------------


def test_a_failed_rollup_prunes_nothing(db, user_id, monkeypatch, capsys):
    """The interlock. Pruning after a failed rollup deletes raw rows whose
    rollup never landed, and past retention the rollup is the only record —
    so the loss is silent and permanent."""
    try:
        old_day = LOCAL_DAY - timedelta(days=40)
        _observe(db, user_id, 9, 0, day=old_day)
        db.commit()
        before = (
            db.query(models.AttendanceObservation)
            .filter(models.AttendanceObservation.user_id == user_id)
            .count()
        )

        def explode(*args, **kwargs):
            raise RuntimeError("rollup is broken")

        pruned = []
        monkeypatch.setattr(rollup_attendance, "rollup_day", explode)
        monkeypatch.setattr(
            rollup_attendance, "prune",
            lambda *a, **k: pruned.append(True) or 0,
        )

        argv = sys.argv
        try:
            sys.argv = ["rollup_attendance.py", "--days", "1"]
            assert rollup_attendance.main() == 1, "a failed rollup must exit non-zero"
        finally:
            sys.argv = argv

        assert pruned == [], "the prune ran after a failed rollup"
        assert (
            db.query(models.AttendanceObservation)
            .filter(models.AttendanceObservation.user_id == user_id)
            .count()
        ) == before
        assert "SKIPPING THE PRUNE" in capsys.readouterr().err
    finally:
        _cleanup(db, user_id)


def test_no_prune_flag_rolls_up_without_deleting(db, user_id, monkeypatch):
    try:
        pruned = []
        monkeypatch.setattr(
            rollup_attendance, "prune",
            lambda *a, **k: pruned.append(True) or 0,
        )
        argv = sys.argv
        try:
            sys.argv = ["rollup_attendance.py", "--days", "1", "--no-prune"]
            assert rollup_attendance.main() == 0
        finally:
            sys.argv = argv
        assert pruned == []
    finally:
        _cleanup(db, user_id)


def test_the_prune_is_the_only_deleter_of_observations():
    """Structural, not behavioural.

    `attendance_observations` is strictly append-only in application code, with
    the retention prune as the sole exception (Q24). A second DELETE anywhere
    breaks the invariant the whole feature rests on, so this greps for one.
    """
    from pathlib import Path

    root = Path(__file__).parent.parent
    offenders = []
    for path in list(root.glob("api/**/*.py")) + list(root.glob("scripts/*.py")):
        if path.name == "rollup_attendance.py":
            continue  # the sanctioned deleter
        source = path.read_text(encoding="utf-8")
        if "AttendanceObservation" not in source:
            continue
        for lineno, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if ".delete(" in stripped and "AttendanceObservation" in source:
                # Narrow to a delete that plausibly targets this table.
                window = "\n".join(source.splitlines()[max(0, lineno - 6):lineno])
                if "AttendanceObservation" in window:
                    offenders.append(f"{path.relative_to(root)}:{lineno}")

    assert not offenders, (
        "attendance_observations is append-only except for the retention "
        f"prune; found another deleter at: {offenders}"
    )
