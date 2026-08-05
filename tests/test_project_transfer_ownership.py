"""Tests for project ownership transfer (PATCH/POST /api/projects/{project_id}/transfer-ownership)."""
import uuid
import pytest
from database import SessionLocal
import models


def test_transfer_project_ownership_success(client, alice, bob):
    suffix = uuid.uuid4().hex[:6]
    alice_headers = {**alice, "X-Annotator-Name": f"alice_{suffix}"}
    
    # Alice creates a team and a project
    team_res = client.post("/api/teams", json={"name": f"Team {suffix}"}, headers=alice_headers)
    assert team_res.status_code == 200, team_res.text
    team_id = team_res.json()["id"]

    p_res = client.post(
        "/api/projects",
        json={
            "name": f"Project Apollo {suffix}",
            "slug": f"project-apollo-{suffix}",
            "type": "Image - Polygon",
            "creator": f"alice_{suffix}",
            "team_id": team_id,
        },
        headers=alice_headers,
    )
    assert p_res.status_code == 200, p_res.text
    proj_id = p_res.json()["id"]

    # Register bob as a TeamMember
    bob_name = f"bob_{suffix}"
    with SessionLocal() as db:
        bob_member = models.TeamMember(name=bob_name, time_logged=0)
        db.add(bob_member)
        db.commit()

    # Alice transfers project ownership to Bob
    transfer_res = client.patch(
        f"/api/projects/{proj_id}/transfer-ownership",
        json={"new_owner": bob_name},
        headers=alice_headers,
    )
    assert transfer_res.status_code == 200, transfer_res.text
    assert transfer_res.json()["creator"] == bob_name

    # Verify in DB: project creator and team association for new owner
    with SessionLocal() as db:
        proj = db.query(models.Project).filter(models.Project.id == proj_id).first()
        assert proj.creator == bob_name

        assoc = db.query(models.TeamMemberAssociation).filter(
            models.TeamMemberAssociation.team_id == team_id,
            models.TeamMemberAssociation.member_name == bob_name,
        ).first()
        assert assoc is not None


def test_transfer_project_ownership_post_alias(client, alice):
    suffix = uuid.uuid4().hex[:6]
    alice_name = f"alice_{suffix}"
    alice_headers = {**alice, "X-Annotator-Name": alice_name}
    p_res = client.post(
        "/api/projects",
        json={"name": f"Project Gemini {suffix}", "slug": f"project-gemini-{suffix}", "type": "Image - Bounding Box", "creator": alice_name},
        headers=alice_headers,
    )
    assert p_res.status_code == 200
    proj_id = p_res.json()["id"]

    charlie_name = f"charlie_{suffix}"
    with SessionLocal() as db:
        charlie_member = models.TeamMember(name=charlie_name, time_logged=0)
        db.add(charlie_member)
        db.commit()

    # Use POST method alias
    transfer_res = client.post(
        f"/api/projects/{proj_id}/transfer-ownership",
        json={"new_owner": charlie_name},
        headers=alice_headers,
    )
    assert transfer_res.status_code == 200, transfer_res.text
    assert transfer_res.json()["creator"] == charlie_name


def test_transfer_project_ownership_non_creator_forbidden(client, alice, bob):
    suffix = uuid.uuid4().hex[:6]
    alice_name = f"alice_{suffix}"
    alice_headers = {**alice, "X-Annotator-Name": alice_name}
    p_res = client.post(
        "/api/projects",
        json={"name": f"Private Project {suffix}", "slug": f"private-project-{suffix}", "type": "Image - Polygon", "creator": alice_name},
        headers=alice_headers,
    )
    assert p_res.status_code == 200
    proj_id = p_res.json()["id"]

    # Bob tries to transfer Alice's project
    bob_headers = {**bob, "X-Annotator-Name": f"bob_{suffix}"}
    res = client.patch(
        f"/api/projects/{proj_id}/transfer-ownership",
        json={"new_owner": f"charlie_{suffix}"},
        headers=bob_headers,
    )
    assert res.status_code == 403
    assert "Only the project creator can transfer ownership" in res.json()["detail"]


def test_transfer_project_ownership_to_self_rejected(client, alice):
    suffix = uuid.uuid4().hex[:6]
    alice_name = f"alice_{suffix}"
    alice_headers = {**alice, "X-Annotator-Name": alice_name}
    p_res = client.post(
        "/api/projects",
        json={"name": f"Solo Project {suffix}", "slug": f"solo-project-{suffix}", "type": "Image - Polygon", "creator": alice_name},
        headers=alice_headers,
    )
    assert p_res.status_code == 200
    proj_id = p_res.json()["id"]

    res = client.patch(
        f"/api/projects/{proj_id}/transfer-ownership",
        json={"new_owner": alice_name},
        headers=alice_headers,
    )
    assert res.status_code == 400
    assert "already the project creator" in res.json()["detail"]


def test_transfer_project_ownership_nonexistent_user_rejected(client, alice):
    suffix = uuid.uuid4().hex[:6]
    alice_name = f"alice_{suffix}"
    alice_headers = {**alice, "X-Annotator-Name": alice_name}
    p_res = client.post(
        "/api/projects",
        json={"name": f"Target Project {suffix}", "slug": f"target-project-{suffix}", "type": "Image - Polygon", "creator": alice_name},
        headers=alice_headers,
    )
    assert p_res.status_code == 200
    proj_id = p_res.json()["id"]

    res = client.patch(
        f"/api/projects/{proj_id}/transfer-ownership",
        json={"new_owner": f"non_existent_{suffix}"},
        headers=alice_headers,
    )
    assert res.status_code == 400
    assert "Target user does not exist" in res.json()["detail"]


def test_transfer_project_ownership_not_found(client, alice):
    alice_headers = {**alice, "X-Annotator-Name": "alice_test"}
    res = client.patch(
        "/api/projects/999999/transfer-ownership",
        json={"new_owner": "someone"},
        headers=alice_headers,
    )
    assert res.status_code == 404
