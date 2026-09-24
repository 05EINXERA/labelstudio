"""Breaks taken from the workspace's Take a break button, and how the Teams
page records them.

A break lives inside a login session, so the cases that matter are the ones
where the session ends without the annotator clicking End break: an explicit
logout, and a tab closed mid-break that the presence sweeper has to close.
"""
import uuid
from datetime import datetime, timezone, timedelta

import models
from database import SessionLocal
from api.presence import close_stale_sessions, as_utc


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


def _member(db, prefix="alice"):
    name = f"{prefix}_{uuid.uuid4().hex[:6]}"
    _mk_team_with_members(db, name, [name])
    return name


def test_start_and_end_break_round_trip(client, alice):
    db = SessionLocal()
    try:
        name = _member(db)
        headers = {**alice, "X-Annotator-Name": name}

        res = client.post("/api/team/breaks", headers=headers)
        assert res.status_code == 201
        started = res.json()
        assert started["is_open"] is True
        assert started["ended_at"] is None

        status = client.get("/api/team/breaks/current", headers=headers).json()
        assert status["on_break"] is True
        assert status["current"]["id"] == started["id"]

        res = client.patch("/api/team/breaks/current", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["on_break"] is False
        assert body["current"]["ended_reason"] == "resumed"
        assert body["current"]["is_open"] is False

        assert client.get("/api/team/breaks/current", headers=headers).json()["on_break"] is False
    finally:
        db.close()


def test_starting_twice_returns_the_same_break(client, alice):
    """A double click or a retried request must not stack a second break."""
    db = SessionLocal()
    try:
        name = _member(db)
        headers = {**alice, "X-Annotator-Name": name}

        first = client.post("/api/team/breaks", headers=headers).json()
        second = client.post("/api/team/breaks", headers=headers).json()
        assert first["id"] == second["id"]
        assert db.query(models.MemberBreak).filter(models.MemberBreak.member_name == name).count() == 1
    finally:
        db.close()


def test_ending_with_no_open_break_is_not_an_error(client, alice):
    """Already ended in another tab, or swept: the client must still be able to leave its break screen."""
    db = SessionLocal()
    try:
        name = _member(db)
        headers = {**alice, "X-Annotator-Name": name}
        res = client.patch("/api/team/breaks/current", headers=headers)
        assert res.status_code == 200
        assert res.json() == {"on_break": False, "current": None}
    finally:
        db.close()


def test_breaks_are_per_annotator_on_the_shared_login(client, alice):
    db = SessionLocal()
    try:
        a = _member(db, "ann")
        b = _member(db, "ben")
        client.post("/api/team/breaks", headers={**alice, "X-Annotator-Name": a})

        assert client.get("/api/team/breaks/current", headers={**alice, "X-Annotator-Name": a}).json()["on_break"] is True
        assert client.get("/api/team/breaks/current", headers={**alice, "X-Annotator-Name": b}).json()["on_break"] is False
    finally:
        db.close()


def test_team_list_shows_member_on_break(client, alice):
    db = SessionLocal()
    try:
        name = _member(db)
        headers = {**alice, "X-Annotator-Name": name}
        client.post("/api/team/breaks", headers=headers)

        rows = {r["name"]: r for r in client.get("/api/team", headers=headers).json()}
        assert rows[name]["is_logged_in"] is True
        assert rows[name]["on_break"] is True

        client.patch("/api/team/breaks/current", headers=headers)
        rows = {r["name"]: r for r in client.get("/api/team", headers=headers).json()}
        assert rows[name]["on_break"] is False
    finally:
        db.close()


def test_history_lists_the_days_breaks_and_totals_them(client, alice):
    db = SessionLocal()
    try:
        name = _member(db)
        headers = {**alice, "X-Annotator-Name": name}

        now = datetime.now(timezone.utc)
        db.add(models.MemberBreak(
            member_name=name,
            started_at=now - timedelta(minutes=40),
            ended_at=now - timedelta(minutes=25),
            ended_reason="resumed",
        ))
        # Yesterday's break must not count toward today.
        db.add(models.MemberBreak(
            member_name=name,
            started_at=now - timedelta(days=1, minutes=30),
            ended_at=now - timedelta(days=1),
            ended_reason="resumed",
        ))
        db.commit()

        body = client.get(f"/api/team/{name}/sessions", headers=headers).json()
        assert len(body["breaks"]) == 1
        assert body["breaks"][0]["duration_seconds"] == 15 * 60
        assert body["break_seconds"] == 15 * 60

        rows = {r["name"]: r for r in client.get("/api/team", headers=headers).json()}
        assert rows[name]["break_seconds_today"] == 15 * 60
    finally:
        db.close()


def test_logout_ends_an_open_break(client, alice):
    db = SessionLocal()
    try:
        name = _member(db)
        headers = {**alice, "X-Annotator-Name": name}
        client.post("/api/team/breaks", headers=headers)
        assert client.post("/api/auth/logout", headers=headers).status_code == 200

        brk = db.query(models.MemberBreak).filter(models.MemberBreak.member_name == name).one()
        db.refresh(brk)
        assert brk.ended_at is not None
        assert brk.ended_reason == "logout"
    finally:
        db.close()


def test_tab_closed_mid_break_is_ended_at_last_heartbeat(client, alice):
    """The sweeper closes the session at its last beat; the break must end there too, not stay open forever."""
    db = SessionLocal()
    try:
        name = _member(db)
        login = datetime.now(timezone.utc) - timedelta(hours=3)
        break_start = login + timedelta(minutes=50)
        last_beat = login + timedelta(hours=1)
        db.add(models.LoginSession(member_name=name, login_at=login, last_seen_at=last_beat))
        db.add(models.MemberBreak(member_name=name, started_at=break_start))
        db.commit()

        close_stale_sessions(db)

        brk = db.query(models.MemberBreak).filter(models.MemberBreak.member_name == name).one()
        db.refresh(brk)
        assert brk.ended_reason == "inactive"
        assert as_utc(brk.ended_at) == last_beat
    finally:
        db.close()


def test_csv_export_carries_break_hours(client, alice):
    db = SessionLocal()
    try:
        name = _member(db)
        headers = {**alice, "X-Annotator-Name": name}
        now = datetime.now(timezone.utc)
        db.add(models.LoginSession(
            member_name=name, login_at=now - timedelta(hours=2),
            last_seen_at=now, logout_at=now, ended_reason="logout",
        ))
        db.add(models.MemberBreak(
            member_name=name, started_at=now - timedelta(hours=1),
            ended_at=now - timedelta(minutes=30), ended_reason="resumed",
        ))
        db.commit()

        res = client.get(f"/api/team/sessions/export?name={name}", headers=headers)
        assert res.status_code == 200
        header, row = res.text.strip().splitlines()[:2]
        cols = header.split(",")
        assert cols[-1] == "break_hours_that_day"
        assert row.split(",")[-1] == "0.50"
    finally:
        db.close()
