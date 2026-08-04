import uuid
from datetime import datetime, timezone, timedelta
import models
from database import SessionLocal


def test_team_creator_sees_logged_in_status_for_active_members(client, alice, bob):
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        bob_name = f"bob_{uuid.uuid4().hex[:6]}"

        # Alice creates team
        team = models.Team(name=f"Alpha Team {uuid.uuid4().hex[:6]}", creator=alice_name)
        db.add(team)
        db.commit()
        db.refresh(team)

        # Alice and Bob team members
        db.add(models.TeamMember(name=alice_name, time_logged=0, last_active_at=datetime.now(timezone.utc)))
        db.add(models.TeamMember(name=bob_name, time_logged=0, last_active_at=datetime.now(timezone.utc)))
        db.add(models.TeamMemberAssociation(member_name=alice_name, team_id=team.id))
        db.add(models.TeamMemberAssociation(member_name=bob_name, team_id=team.id))
        db.commit()

        alice_headers = {**alice, "X-Annotator-Name": alice_name}
        res = client.get("/api/team", headers=alice_headers)
        assert res.status_code == 200
        data = res.json()

        bob_entry = next((m for m in data if m["name"] == bob_name), None)
        assert bob_entry is not None
        assert bob_entry["is_logged_in"] is True
        assert bob_entry["last_active_at"] is not None
    finally:
        db.close()


def test_team_creator_sees_offline_status_for_inactive_members(client, alice):
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        charlie_name = f"charlie_{uuid.uuid4().hex[:6]}"

        team = models.Team(name=f"Beta Team {uuid.uuid4().hex[:6]}", creator=alice_name)
        db.add(team)
        db.commit()
        db.refresh(team)

        # Charlie was active 10 minutes ago
        past_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        db.add(models.TeamMember(name=alice_name, time_logged=0, last_active_at=datetime.now(timezone.utc)))
        db.add(models.TeamMember(name=charlie_name, time_logged=0, last_active_at=past_time))
        db.add(models.TeamMemberAssociation(member_name=alice_name, team_id=team.id))
        db.add(models.TeamMemberAssociation(member_name=charlie_name, team_id=team.id))
        db.commit()

        alice_headers = {**alice, "X-Annotator-Name": alice_name}
        res = client.get("/api/team", headers=alice_headers)
        assert res.status_code == 200
        data = res.json()

        charlie_entry = next((m for m in data if m["name"] == charlie_name), None)
        assert charlie_entry is not None
        assert charlie_entry["is_logged_in"] is False
    finally:
        db.close()


def test_non_creator_cannot_see_presence_status(client, alice, bob):
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        bob_name = f"bob_{uuid.uuid4().hex[:6]}"

        # Alice creates team
        team = models.Team(name=f"Gamma Team {uuid.uuid4().hex[:6]}", creator=alice_name)
        db.add(team)
        db.commit()
        db.refresh(team)

        # Add both to team
        db.add(models.TeamMember(name=alice_name, time_logged=0, last_active_at=datetime.now(timezone.utc)))
        db.add(models.TeamMember(name=bob_name, time_logged=0, last_active_at=datetime.now(timezone.utc)))
        db.add(models.TeamMemberAssociation(member_name=alice_name, team_id=team.id))
        db.add(models.TeamMemberAssociation(member_name=bob_name, team_id=team.id))
        db.commit()

        # Bob (not creator) queries team list
        bob_headers = {**bob, "X-Annotator-Name": bob_name}
        res = client.get("/api/team", headers=bob_headers)
        assert res.status_code == 200
        data = res.json()

        alice_entry = next((m for m in data if m["name"] == alice_name), None)
        assert alice_entry is not None
        # Bob did not create the team, so is_logged_in is None
        assert alice_entry["is_logged_in"] is None
    finally:
        db.close()


def test_presence_ping_updates_last_active_at(client, alice):
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        old_time = datetime.now(timezone.utc) - timedelta(minutes=15)
        db.add(models.TeamMember(name=alice_name, time_logged=0, last_active_at=old_time))
        db.commit()

        alice_headers = {**alice, "X-Annotator-Name": alice_name}
        res = client.post("/api/team/ping", headers=alice_headers)
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

        member = db.query(models.TeamMember).filter(models.TeamMember.name == alice_name).first()
        db.refresh(member)
        assert member.last_active_at is not None
        # Must be recent (within 5 seconds)
        assert (datetime.now(timezone.utc) - member.last_active_at.replace(tzinfo=timezone.utc)).total_seconds() < 5
    finally:
        db.close()


def test_logout_clears_last_active_at(client, alice):
    db = SessionLocal()
    try:
        alice_name = f"alice_{uuid.uuid4().hex[:6]}"
        db.add(models.TeamMember(name=alice_name, time_logged=0, last_active_at=datetime.now(timezone.utc)))
        db.commit()

        alice_headers = {**alice, "X-Annotator-Name": alice_name}
        res = client.post("/api/auth/logout", headers=alice_headers)
        assert res.status_code == 200

        member = db.query(models.TeamMember).filter(models.TeamMember.name == alice_name).first()
        db.refresh(member)
        assert member.last_active_at is None
    finally:
        db.close()
