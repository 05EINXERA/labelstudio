"""Tests for team ownership transfer (PATCH /api/teams/{team_id}/transfer-ownership)."""
import pytest
from database import SessionLocal
import models


def test_transfer_team_ownership_success(client, alice, bob):
    # Alice creates a team
    alice_headers = {**alice, "X-Annotator-Name": "alice_annotator"}
    team_res = client.post("/api/teams", json={"name": "Engineering Team"}, headers=alice_headers)
    assert team_res.status_code == 200, team_res.text
    team_data = team_res.json()
    team_id = team_data["id"]
    assert team_data["creator"] == "alice_annotator"

    # Alice creates two projects assigned to this team
    p1_res = client.post("/api/projects", json={"name": "Proj 1", "slug": "proj-1", "type": "detection", "creator": "alice_annotator", "team_id": team_id}, headers=alice_headers)
    assert p1_res.status_code == 200, p1_res.text
    p1_id = p1_res.json()["id"]

    p2_res = client.post("/api/projects", json={"name": "Proj 2", "slug": "proj-2", "type": "segmentation", "creator": "alice_annotator", "team_id": team_id}, headers=alice_headers)
    assert p2_res.status_code == 200, p2_res.text
    p2_id = p2_res.json()["id"]

    # Register bob_annotator as a TeamMember
    with SessionLocal() as db:
        bob_member = models.TeamMember(name="bob_annotator", time_logged=0)
        db.add(bob_member)
        db.commit()

    # Alice transfers ownership to Bob
    transfer_res = client.patch(
        f"/api/teams/{team_id}/transfer-ownership",
        json={"new_owner": "bob_annotator"},
        headers=alice_headers,
    )
    assert transfer_res.status_code == 200, transfer_res.text
    transferred = transfer_res.json()
    assert transferred["creator"] == "bob_annotator"

    # Verify in database: team creator, associations, and projects
    with SessionLocal() as db:
        team = db.query(models.Team).filter(models.Team.id == team_id).first()
        assert team.creator == "bob_annotator"
        # Bob is also in team associations
        assoc = db.query(models.TeamMemberAssociation).filter(
            models.TeamMemberAssociation.team_id == team_id,
            models.TeamMemberAssociation.member_name == "bob_annotator",
        ).first()
        assert assoc is not None

        # Verify all team projects have their creator updated to bob_annotator
        p1 = db.query(models.Project).filter(models.Project.id == p1_id).first()
        p2 = db.query(models.Project).filter(models.Project.id == p2_id).first()
        assert p1.creator == "bob_annotator"
        assert p2.creator == "bob_annotator"


def test_transfer_team_ownership_non_creator_forbidden(client, alice, bob):
    alice_headers = {**alice, "X-Annotator-Name": "alice_annotator"}
    team_res = client.post("/api/teams", json={"name": "Design Team"}, headers=alice_headers)
    assert team_res.status_code == 200
    team_id = team_res.json()["id"]

    # Bob tries to transfer ownership of Alice's team
    bob_headers = {**bob, "X-Annotator-Name": "bob_annotator"}
    res = client.patch(
        f"/api/teams/{team_id}/transfer-ownership",
        json={"new_owner": "charlie"},
        headers=bob_headers,
    )
    assert res.status_code == 403
    assert "Only the team creator can transfer ownership" in res.json()["detail"]


def test_transfer_team_ownership_to_self_rejected(client, alice):
    alice_headers = {**alice, "X-Annotator-Name": "alice_annotator"}
    team_res = client.post("/api/teams", json={"name": "Product Team"}, headers=alice_headers)
    assert team_res.status_code == 200
    team_id = team_res.json()["id"]

    res = client.patch(
        f"/api/teams/{team_id}/transfer-ownership",
        json={"new_owner": "alice_annotator"},
        headers=alice_headers,
    )
    assert res.status_code == 400
    assert "already the team creator" in res.json()["detail"]


def test_transfer_team_ownership_nonexistent_user_rejected(client, alice):
    alice_headers = {**alice, "X-Annotator-Name": "alice_annotator"}
    team_res = client.post("/api/teams", json={"name": "Marketing Team"}, headers=alice_headers)
    assert team_res.status_code == 200
    team_id = team_res.json()["id"]

    res = client.patch(
        f"/api/teams/{team_id}/transfer-ownership",
        json={"new_owner": "ghost_user_999"},
        headers=alice_headers,
    )
    assert res.status_code == 400
    assert "Target user does not exist" in res.json()["detail"]


def test_transfer_team_ownership_nonexistent_team_404(client, alice):
    alice_headers = {**alice, "X-Annotator-Name": "alice_annotator"}
    res = client.patch(
        "/api/teams/999999/transfer-ownership",
        json={"new_owner": "bob_annotator"},
        headers=alice_headers,
    )
    assert res.status_code == 404
