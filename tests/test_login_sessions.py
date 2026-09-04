"""Login/logout session history behind the Teams page.

The interesting cases are the ones that are not a clean login→logout: an
annotator who closes the tab, one who comes back after lunch, and one whose
hours another annotator must not be able to read.
"""
import uuid
from datetime import datetime, timezone, timedelta

import models
from database import SessionLocal
from api.presence import close_stale_sessions, open_session, touch_session, close_session


def _mk_team_with_members(db, creator, members):
    team = models.Team(name=f"Team {uuid.uuid4().hex[:8]}", creator=creator)
    db.add(team)
    db.commit()
    db.refresh(team)
    for name in members:
        if not db.query(models.TeamMember).filter(models.TeamMember.name == name).first():
            db.add(models.TeamMember(name=name, time_logged=0, last_active_at=datetime.now(timezone.utc)))
        db.add(models.TeamMemberAssociation(member_name=name, team_id=team.id))
    db.commit()
    return team


def test_ping_opens_a_session_and_history_lists_it(client, alice):
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        _mk_team_with_members(db, alice_name, [alice_name])

        headers = {**alice, "X-Annotator-Name": alice_name}
        assert client.post("/api/team/ping", headers=headers).status_code == 200

        res = client.get(f"/api/team/{alice_name}/sessions", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert len(body["sessions"]) == 1
        # Still live, so no logout time yet.
        assert body["sessions"][0]["is_open"] is True
        assert body["sessions"][0]["logout_at"] is None
    finally:
        db.close()


def test_explicit_logout_closes_the_session(client, alice):
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        _mk_team_with_members(db, alice_name, [alice_name])
        headers = {**alice, "X-Annotator-Name": alice_name}

        client.post("/api/team/ping", headers=headers)
        assert client.post("/api/auth/logout", headers=headers).status_code == 200

        session = (
            db.query(models.LoginSession)
            .filter(models.LoginSession.member_name == alice_name)
            .one()
        )
        db.refresh(session)
        assert session.logout_at is not None
        assert session.ended_reason == "logout"
    finally:
        db.close()


def test_abandoned_session_is_closed_at_last_heartbeat(client, alice):
    """A closed tab leaves no logout event; the sweeper must not use "now"."""
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        _mk_team_with_members(db, alice_name, [alice_name])

        login = datetime.now(timezone.utc) - timedelta(hours=3)
        last_beat = login + timedelta(hours=1)
        db.add(models.LoginSession(
            member_name=alice_name, login_at=login, last_seen_at=last_beat,
        ))
        db.commit()

        assert close_stale_sessions(db) >= 1

        session = (
            db.query(models.LoginSession)
            .filter(models.LoginSession.member_name == alice_name)
            .one()
        )
        db.refresh(session)
        assert session.ended_reason == "inactive"
        # Logout is stamped at the last beat we heard, ~2h ago — not now.
        assert abs((session.logout_at.replace(tzinfo=timezone.utc) - last_beat).total_seconds()) < 2
        assert session.logout_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc) - timedelta(hours=1)
    finally:
        db.close()


def test_live_session_is_not_swept(client, alice):
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        db.add(models.TeamMember(name=alice_name, time_logged=0))
        db.commit()
        touch_session(db, alice_name)

        close_stale_sessions(db)

        session = db.query(models.LoginSession).filter(
            models.LoginSession.member_name == alice_name
        ).one()
        db.refresh(session)
        assert session.logout_at is None
    finally:
        db.close()


def test_return_after_a_gap_starts_a_second_session(client, alice):
    """A lunch break must read as two sessions, not one long one."""
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        _mk_team_with_members(db, alice_name, [alice_name])

        morning = datetime.now(timezone.utc) - timedelta(hours=4)
        db.add(models.LoginSession(
            member_name=alice_name, login_at=morning, last_seen_at=morning + timedelta(hours=1),
        ))
        db.commit()

        # Heartbeat resumes hours later: the stale row closes, a new one opens.
        touch_session(db, alice_name)

        rows = (
            db.query(models.LoginSession)
            .filter(models.LoginSession.member_name == alice_name)
            .order_by(models.LoginSession.login_at.asc())
            .all()
        )
        assert len(rows) == 2
        assert rows[0].logout_at is not None
        assert rows[0].ended_reason == "inactive"
        assert rows[1].logout_at is None
    finally:
        db.close()


def test_new_login_supersedes_a_session_left_open(client, alice):
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        db.add(models.TeamMember(name=alice_name, time_logged=0))
        db.commit()

        open_session(db, alice_name)
        open_session(db, alice_name)

        rows = db.query(models.LoginSession).filter(
            models.LoginSession.member_name == alice_name
        ).all()
        # Exactly one may be open at a time, otherwise totals double-count.
        assert sum(1 for r in rows if r.logout_at is None) == 1
    finally:
        db.close()


def test_history_totals_only_the_requested_day(client, alice):
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        _mk_team_with_members(db, alice_name, [alice_name])
        headers = {**alice, "X-Annotator-Name": alice_name}

        now = datetime.now(timezone.utc)
        today = now.replace(hour=6, minute=0, second=0, microsecond=0)
        yesterday = today - timedelta(days=1)
        # 2h today, 5h yesterday.
        db.add(models.LoginSession(
            member_name=alice_name, login_at=today,
            last_seen_at=today + timedelta(hours=2),
            logout_at=today + timedelta(hours=2), ended_reason="logout",
        ))
        db.add(models.LoginSession(
            member_name=alice_name, login_at=yesterday,
            last_seen_at=yesterday + timedelta(hours=5),
            logout_at=yesterday + timedelta(hours=5), ended_reason="logout",
        ))
        db.commit()

        res = client.get(
            f"/api/team/{alice_name}/sessions",
            params={"date": today.date().isoformat(), "tz_offset": 0},
            headers=headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert len(body["sessions"]) == 1
        assert body["total_seconds"] == 2 * 3600

        res = client.get(
            f"/api/team/{alice_name}/sessions",
            params={"date": yesterday.date().isoformat(), "tz_offset": 0},
            headers=headers,
        )
        assert res.json()["total_seconds"] == 5 * 3600
    finally:
        db.close()


def test_non_owner_cannot_read_another_members_history(client, alice, bob):
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        bob_name = f"bob_{uuid.uuid4().hex[:6]}"
        # Alice owns the team; Bob is just a member.
        _mk_team_with_members(db, alice_name, [alice_name, bob_name])

        bob_headers = {**bob, "X-Annotator-Name": bob_name}
        res = client.get(f"/api/team/{alice_name}/sessions", headers=bob_headers)
        assert res.status_code == 403

        # But Bob may always read his own.
        res = client.get(f"/api/team/{bob_name}/sessions", headers=bob_headers)
        assert res.status_code == 200
    finally:
        db.close()


def test_team_list_reports_seconds_today(client, alice):
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        _mk_team_with_members(db, alice_name, [alice_name])

        now = datetime.now(timezone.utc)
        start = now.replace(hour=1, minute=0, second=0, microsecond=0)
        db.add(models.LoginSession(
            member_name=alice_name, login_at=start,
            last_seen_at=start + timedelta(hours=3),
            logout_at=start + timedelta(hours=3), ended_reason="logout",
        ))
        db.commit()

        headers = {**alice, "X-Annotator-Name": alice_name}
        res = client.get("/api/team", params={"tz_offset": 0}, headers=headers)
        assert res.status_code == 200
        entry = next(m for m in res.json() if m["name"] == alice_name)
        assert entry["seconds_today"] == 3 * 3600
    finally:
        db.close()


def _csv_rows(text):
    import csv as _csv, io as _io
    return list(_csv.DictReader(_io.StringIO(text)))


def test_csv_export_for_one_member(client, alice):
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        _mk_team_with_members(db, alice_name, [alice_name])
        headers = {**alice, "X-Annotator-Name": alice_name}

        day = datetime.now(timezone.utc).replace(hour=2, minute=0, second=0, microsecond=0)
        # Two sessions the same day: the day's total must repeat on both rows.
        db.add(models.LoginSession(
            member_name=alice_name, login_at=day, last_seen_at=day + timedelta(hours=2),
            logout_at=day + timedelta(hours=2), ended_reason="logout"))
        db.add(models.LoginSession(
            member_name=alice_name, login_at=day + timedelta(hours=3),
            last_seen_at=day + timedelta(hours=4),
            logout_at=day + timedelta(hours=4), ended_reason="inactive"))
        db.commit()

        res = client.get(
            "/api/team/sessions/export",
            params={"name": alice_name, "start": day.date().isoformat(),
                    "end": day.date().isoformat(), "tz_offset": 0},
            headers=headers,
        )
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/csv")
        assert "attachment" in res.headers["content-disposition"]

        rows = _csv_rows(res.text)
        assert len(rows) == 2
        assert rows[0]["duration_hours"] == "2.00"
        assert rows[1]["ended"] == "inactive"
        # Both rows carry the same daily total so a pivot can roll it up.
        assert rows[0]["total_hours_that_day"] == "3.00"
        assert rows[1]["total_hours_that_day"] == "3.00"
    finally:
        db.close()


def test_csv_export_covers_whole_team_when_no_name_given(client, alice):
    db = SessionLocal()
    try:
        boss = f"boss_{uuid.uuid4().hex[:6]}"
        worker = f"worker_{uuid.uuid4().hex[:6]}"
        _mk_team_with_members(db, boss, [boss, worker])
        headers = {**alice, "X-Annotator-Name": boss}

        day = datetime.now(timezone.utc).replace(hour=2, minute=0, second=0, microsecond=0)
        db.add(models.LoginSession(
            member_name=worker, login_at=day, last_seen_at=day + timedelta(hours=1),
            logout_at=day + timedelta(hours=1), ended_reason="logout"))
        db.commit()

        res = client.get(
            "/api/team/sessions/export",
            params={"start": day.date().isoformat(), "end": day.date().isoformat(), "tz_offset": 0},
            headers=headers,
        )
        assert res.status_code == 200
        names = {r["annotator"] for r in _csv_rows(res.text)}
        assert worker in names
    finally:
        db.close()


def test_csv_export_excludes_members_of_teams_i_do_not_own(client, alice, bob):
    """A member must not appear in another annotator's export."""
    db = SessionLocal()
    try:
        outsider = f"outsider_{uuid.uuid4().hex[:6]}"
        bob_name = f"bob_{uuid.uuid4().hex[:6]}"
        # Two unrelated teams with different creators.
        _mk_team_with_members(db, outsider, [outsider])
        _mk_team_with_members(db, bob_name, [bob_name])

        day = datetime.now(timezone.utc).replace(hour=2, minute=0, second=0, microsecond=0)
        db.add(models.LoginSession(
            member_name=outsider, login_at=day, last_seen_at=day + timedelta(hours=1),
            logout_at=day + timedelta(hours=1), ended_reason="logout"))
        db.commit()

        bob_headers = {**bob, "X-Annotator-Name": bob_name}
        res = client.get(
            "/api/team/sessions/export",
            params={"start": day.date().isoformat(), "end": day.date().isoformat(), "tz_offset": 0},
            headers=bob_headers,
        )
        assert res.status_code == 200
        assert outsider not in {r["annotator"] for r in _csv_rows(res.text)}

        # And naming them directly is refused outright.
        res = client.get(
            "/api/team/sessions/export", params={"name": outsider}, headers=bob_headers
        )
        assert res.status_code == 403
    finally:
        db.close()


def test_csv_export_rejects_bad_and_oversized_ranges(client, alice):
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        _mk_team_with_members(db, alice_name, [alice_name])
        headers = {**alice, "X-Annotator-Name": alice_name}

        res = client.get("/api/team/sessions/export",
                         params={"start": "2020-01-01", "end": "2026-01-01"}, headers=headers)
        assert res.status_code == 400

        res = client.get("/api/team/sessions/export",
                         params={"start": "04-09-2026"}, headers=headers)
        assert res.status_code == 400

        res = client.get("/api/team/sessions/export",
                         params={"start": "2026-09-04", "end": "2026-09-01"}, headers=headers)
        assert res.status_code == 400
    finally:
        db.close()


def test_bad_date_is_rejected(client, alice):
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        _mk_team_with_members(db, alice_name, [alice_name])
        headers = {**alice, "X-Annotator-Name": alice_name}
        res = client.get(
            f"/api/team/{alice_name}/sessions", params={"date": "04-09-2026"}, headers=headers
        )
        assert res.status_code == 400
    finally:
        db.close()
