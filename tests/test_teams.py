"""Teams API — `/api/teams` and `GET /api/auth/me`.

Spec: .devnotes/teams/03_API.md §§ 2, 5. Edge cases: 06_EDGE_CASES.md E-01
through E-06, E-14, E-21, E-23.

Phase 2 manages teams only. Project *access* is still `owner_id`-based until
Phase 3, so nothing here should change what any existing endpoint returns.
"""
import pytest

import models
from api.rate_limit import reset_rate_limit
from config import MAX_TEAMS_PER_USER
from database import SessionLocal


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """The limiter is module state and the suite does not restart between
    tests, so one test exhausting an allowance would leak into the next."""
    reset_rate_limit()
    yield
    reset_rate_limit()


def _create(client, headers, name="Alpha", description=None):
    return client.post(
        "/api/teams",
        json={"name": name, "description": description},
        headers=headers,
    )


def _username(client, headers):
    return client.get("/api/auth/me", headers=headers).json()["username"]


# --- creation ----------------------------------------------------------------


def test_create_team_makes_creator_owner(client, alice):
    res = _create(client, alice, "Alpha")

    assert res.status_code == 201, res.text
    body = res.json()
    assert body["my_role"] == "owner"
    assert body["is_owner"] is True
    # The owner membership row is written with the team, not lazily: a team
    # whose owner is not a member is invisible to every roster query.
    assert [m["role"] for m in body["members"]] == ["owner"]
    assert body["members"][0]["username"] == _username(client, alice)


def test_create_team_derives_a_slug(client, alice):
    body = _create(client, alice, "My Great Team!").json()

    assert body["slug"] == "my-great-team"


def test_slug_collision_gets_suffix(client, alice, bob):
    """Two people naming a team the same is normal; a 409 for something the
    user did not type and cannot fix is a dead end (02_SCHEMA.md § 2).

    The name is unique to this test because the schema is created once per
    session — a shared name would make the expected suffix depend on how many
    other tests ran first.
    """
    name = "Slug Collision Probe"

    first = _create(client, alice, name).json()
    second = _create(client, bob, name).json()

    assert first["slug"] == "slug-collision-probe"
    assert second["slug"] == "slug-collision-probe-2"
    assert first["slug"] != second["slug"]


def test_slug_never_empty_for_punctuation_only_name(client, alice):
    """A name of pure punctuation must still produce a usable uniqueness key."""
    body = _create(client, alice, "!!!").json()

    assert body["slug"]


def test_max_teams_per_user_enforced(client, alice):
    for i in range(MAX_TEAMS_PER_USER):
        assert _create(client, alice, f"T{i}").status_code == 201

    res = _create(client, alice, "One too many")

    assert res.status_code == 409
    assert str(MAX_TEAMS_PER_USER) in res.json()["detail"]


# --- listing and visibility --------------------------------------------------


def test_list_teams_only_returns_own(client, alice, bob):
    _create(client, alice, "Alpha")
    _create(client, bob, "Beta")

    alice_teams = client.get("/api/teams", headers=alice).json()
    bob_teams = client.get("/api/teams", headers=bob).json()

    assert [t["name"] for t in alice_teams] == ["Alpha"]
    assert [t["name"] for t in bob_teams] == ["Beta"]


def test_non_member_gets_404(client, alice, bob):
    """404 not 403: a team you are not in is indistinguishable from one that
    does not exist, which is what stops team-id probing (E-16)."""
    team_id = _create(client, alice, "Alpha").json()["id"]

    res = client.get(f"/api/teams/{team_id}", headers=bob)

    assert res.status_code == 404
    assert res.json()["detail"] == "Team not found"


def test_team_summary_counts_members(client, alice, bob):
    team_id = _create(client, alice, "Alpha").json()["id"]
    client.post(
        f"/api/teams/{team_id}/members",
        json={"username": _username(client, bob), "role": "member"},
        headers=alice,
    )

    summary = client.get("/api/teams", headers=alice).json()[0]

    assert summary["member_count"] == 2
    assert summary["project_count"] == 0


# --- adding members ----------------------------------------------------------


def test_add_member_by_username(client, alice, bob):
    team_id = _create(client, alice, "Alpha").json()["id"]
    bob_name = _username(client, bob)

    res = client.post(
        f"/api/teams/{team_id}/members",
        json={"username": bob_name, "role": "member"},
        headers=alice,
    )

    assert res.status_code == 200, res.text
    assert res.json()["username"] == bob_name
    assert res.json()["role"] == "member"
    # No acceptance step: the member is immediately in the team (§ 5.2).
    assert client.get(f"/api/teams/{team_id}", headers=bob).status_code == 200


def test_add_unknown_username_404(client, alice):
    """E-02: an invite flow that cannot say "you typo'd" is unusable, so the
    username disclosure here is accepted and scoped by the rate limit."""
    team_id = _create(client, alice, "Alpha").json()["id"]

    res = client.post(
        f"/api/teams/{team_id}/members",
        json={"username": "nobody-by-that-name", "role": "member"},
        headers=alice,
    )

    assert res.status_code == 404
    assert "nobody-by-that-name" in res.json()["detail"]


def test_double_add_is_idempotent(client, alice, bob):
    """E-01/E-21: adding someone twice is a double-click, not an error. Handled
    by catching IntegrityError rather than a pre-check, which races."""
    team_id = _create(client, alice, "Alpha").json()["id"]
    bob_name = _username(client, bob)
    payload = {"username": bob_name, "role": "member"}

    first = client.post(f"/api/teams/{team_id}/members", json=payload, headers=alice)
    second = client.post(f"/api/teams/{team_id}/members", json=payload, headers=alice)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["role"] == "member"
    assert len(client.get(f"/api/teams/{team_id}/members", headers=alice).json()) == 2


def test_double_add_does_not_downgrade_an_existing_role(client, alice, bob):
    """The idempotent path echoes the *current* role rather than overwriting
    it — a re-add must not silently demote a manager back to member."""
    team_id = _create(client, alice, "Alpha").json()["id"]
    bob_name = _username(client, bob)
    client.post(
        f"/api/teams/{team_id}/members",
        json={"username": bob_name, "role": "manager"},
        headers=alice,
    )

    second = client.post(
        f"/api/teams/{team_id}/members",
        json={"username": bob_name, "role": "member"},
        headers=alice,
    )

    assert second.json()["role"] == "manager"


def test_add_member_requires_manager(client, alice, bob):
    team_id = _create(client, alice, "Alpha").json()["id"]
    bob_name = _username(client, bob)
    client.post(
        f"/api/teams/{team_id}/members",
        json={"username": bob_name, "role": "member"},
        headers=alice,
    )

    res = client.post(
        f"/api/teams/{team_id}/members",
        json={"username": bob_name, "role": "member"},
        headers=bob,
    )

    assert res.status_code == 403


def test_invalid_role_is_422(client, alice, bob):
    """Roles are a Pydantic Literal, so a bad role never reaches the database."""
    team_id = _create(client, alice, "Alpha").json()["id"]

    res = client.post(
        f"/api/teams/{team_id}/members",
        json={"username": _username(client, bob), "role": "superuser"},
        headers=alice,
    )

    assert res.status_code == 422


def test_add_member_cannot_set_owner_role(client, alice, bob):
    """Ownership moves by transfer only: a team must never have two owners."""
    team_id = _create(client, alice, "Alpha").json()["id"]

    res = client.post(
        f"/api/teams/{team_id}/members",
        json={"username": _username(client, bob), "role": "owner"},
        headers=alice,
    )

    assert res.status_code == 422


def test_add_member_rate_limited(client, alice, bob):
    """E-14: the limit is what keeps username disclosure a scoped trade rather
    than a bulk enumeration oracle."""
    team_id = _create(client, alice, "Alpha").json()["id"]

    statuses = []
    for _ in range(35):
        statuses.append(
            client.post(
                f"/api/teams/{team_id}/members",
                json={"username": "no-such-user", "role": "member"},
                headers=alice,
            ).status_code
        )

    assert 429 in statuses
    assert statuses.count(404) == 30, "the configured allowance should be spent first"


def test_rate_limit_is_per_user(client, alice, bob):
    """One caller exhausting their allowance must not lock out everyone else."""
    alice_team = _create(client, alice, "Alpha").json()["id"]
    bob_team = _create(client, bob, "Beta").json()["id"]
    for _ in range(31):
        client.post(
            f"/api/teams/{alice_team}/members",
            json={"username": "no-such-user", "role": "member"},
            headers=alice,
        )

    res = client.post(
        f"/api/teams/{bob_team}/members",
        json={"username": "also-no-such-user", "role": "member"},
        headers=bob,
    )

    assert res.status_code == 404


# --- role changes ------------------------------------------------------------


def test_manager_cannot_promote_above_own_role(client, alice, bob):
    """E-03: without this a manager self-escalates by proxy."""
    team_id = _create(client, alice, "Alpha").json()["id"]
    bob_name = _username(client, bob)
    client.post(
        f"/api/teams/{team_id}/members",
        json={"username": bob_name, "role": "manager"},
        headers=alice,
    )
    bob_id = client.get("/api/auth/me", headers=bob).json()["id"]

    # A manager may set member/manager, and the Literal already blocks owner.
    allowed = client.patch(
        f"/api/teams/{team_id}/members/{bob_id}", json={"role": "member"}, headers=bob
    )
    rejected = client.patch(
        f"/api/teams/{team_id}/members/{bob_id}", json={"role": "owner"}, headers=bob
    )

    assert allowed.status_code == 200
    assert rejected.status_code == 422


def test_member_cannot_change_roles(client, alice, bob):
    team_id = _create(client, alice, "Alpha").json()["id"]
    bob_name = _username(client, bob)
    client.post(
        f"/api/teams/{team_id}/members",
        json={"username": bob_name, "role": "member"},
        headers=alice,
    )
    bob_id = client.get("/api/auth/me", headers=bob).json()["id"]

    res = client.patch(
        f"/api/teams/{team_id}/members/{bob_id}", json={"role": "manager"}, headers=bob
    )

    assert res.status_code == 403


def test_owner_role_cannot_be_changed_by_patch(client, alice, bob):
    team_id = _create(client, alice, "Alpha").json()["id"]
    alice_id = client.get("/api/auth/me", headers=alice).json()["id"]
    client.post(
        f"/api/teams/{team_id}/members",
        json={"username": _username(client, bob), "role": "manager"},
        headers=alice,
    )

    res = client.patch(
        f"/api/teams/{team_id}/members/{alice_id}", json={"role": "member"}, headers=bob
    )

    assert res.status_code == 409


# --- leaving and removal -----------------------------------------------------


def test_member_can_leave(client, alice, bob):
    """The safety valve that makes "no acceptance flow" acceptable (§ 5.2)."""
    team_id = _create(client, alice, "Alpha").json()["id"]
    client.post(
        f"/api/teams/{team_id}/members",
        json={"username": _username(client, bob), "role": "member"},
        headers=alice,
    )

    res = client.delete(f"/api/teams/{team_id}/members/me", headers=bob)

    assert res.status_code == 200
    assert client.get(f"/api/teams/{team_id}", headers=bob).status_code == 404


def test_owner_cannot_leave(client, alice):
    """E-05: a team always has exactly one owner."""
    team_id = _create(client, alice, "Alpha").json()["id"]

    res = client.delete(f"/api/teams/{team_id}/members/me", headers=alice)

    assert res.status_code == 409
    assert "Transfer ownership" in res.json()["detail"]


def test_owner_cannot_be_removed(client, alice, bob):
    team_id = _create(client, alice, "Alpha").json()["id"]
    alice_id = client.get("/api/auth/me", headers=alice).json()["id"]
    client.post(
        f"/api/teams/{team_id}/members",
        json={"username": _username(client, bob), "role": "manager"},
        headers=alice,
    )

    res = client.delete(f"/api/teams/{team_id}/members/{alice_id}", headers=bob)

    assert res.status_code == 409


def test_manager_can_remove_another_manager(client, alice, bob):
    """E-04: managers are peers; only the owner is protected."""
    team_id = _create(client, alice, "Alpha").json()["id"]
    bob_name = _username(client, bob)
    client.post(
        f"/api/teams/{team_id}/members",
        json={"username": bob_name, "role": "manager"},
        headers=alice,
    )
    bob_id = client.get("/api/auth/me", headers=bob).json()["id"]

    res = client.delete(f"/api/teams/{team_id}/members/{bob_id}", headers=alice)

    assert res.status_code == 200


# --- transfer ----------------------------------------------------------------


def test_transfer_ownership_updates_both_rows(client, alice, bob):
    """E-05/E-23: `Team.owner_id` and the membership rows are two views of one
    invariant and must move together, in one transaction."""
    team_id = _create(client, alice, "Alpha").json()["id"]
    bob_name = _username(client, bob)
    client.post(
        f"/api/teams/{team_id}/members",
        json={"username": bob_name, "role": "member"},
        headers=alice,
    )

    res = client.post(
        f"/api/teams/{team_id}/transfer", json={"username": bob_name}, headers=alice
    )

    assert res.status_code == 200, res.text
    roles = {m["username"]: m["role"] for m in res.json()["members"]}
    assert roles[bob_name] == "owner"
    assert roles[_username(client, alice)] == "manager"

    db = SessionLocal()
    try:
        team = db.get(models.Team, team_id)
        bob_user = db.query(models.User).filter(models.User.username == bob_name).one()
        assert team.owner_id == bob_user.id, "denormalised owner_id must follow"
    finally:
        db.close()


def test_transfer_to_non_member_404(client, alice, bob):
    team_id = _create(client, alice, "Alpha").json()["id"]

    res = client.post(
        f"/api/teams/{team_id}/transfer",
        json={"username": _username(client, bob)},
        headers=alice,
    )

    assert res.status_code == 404


def test_transfer_requires_owner(client, alice, bob):
    team_id = _create(client, alice, "Alpha").json()["id"]
    bob_name = _username(client, bob)
    client.post(
        f"/api/teams/{team_id}/members",
        json={"username": bob_name, "role": "manager"},
        headers=alice,
    )

    res = client.post(
        f"/api/teams/{team_id}/transfer", json={"username": bob_name}, headers=bob
    )

    assert res.status_code == 403


def test_owner_can_leave_after_transferring(client, alice, bob):
    team_id = _create(client, alice, "Alpha").json()["id"]
    bob_name = _username(client, bob)
    client.post(
        f"/api/teams/{team_id}/members",
        json={"username": bob_name, "role": "member"},
        headers=alice,
    )
    client.post(
        f"/api/teams/{team_id}/transfer", json={"username": bob_name}, headers=alice
    )

    res = client.delete(f"/api/teams/{team_id}/members/me", headers=alice)

    assert res.status_code == 200


# --- rename ------------------------------------------------------------------


def test_rename_reslugs(client, alice):
    team_id = _create(client, alice, "Alpha").json()["id"]

    res = client.patch(
        f"/api/teams/{team_id}", json={"name": "Renamed Team"}, headers=alice
    )

    assert res.status_code == 200
    assert res.json()["name"] == "Renamed Team"
    assert res.json()["slug"] == "renamed-team"


def test_rename_does_not_collide_with_itself(client, alice):
    """Re-saving the same name must not append a suffix to its own slug.

    Asserted as "the slug is unchanged" rather than against a literal: other
    tests in the session also create teams named Alpha, so this team's slug may
    legitimately be `alpha-7`. The invariant under test is that a team does not
    collide with *itself*, whatever its slug happens to be.
    """
    created = _create(client, alice, "Alpha").json()

    res = client.patch(
        f"/api/teams/{created['id']}", json={"name": "Alpha"}, headers=alice
    )

    assert res.json()["slug"] == created["slug"]


def test_rename_requires_manager(client, alice, bob):
    team_id = _create(client, alice, "Alpha").json()["id"]
    client.post(
        f"/api/teams/{team_id}/members",
        json={"username": _username(client, bob), "role": "member"},
        headers=alice,
    )

    res = client.patch(f"/api/teams/{team_id}", json={"name": "Nope"}, headers=bob)

    assert res.status_code == 403


# --- deletion ----------------------------------------------------------------


def test_delete_team_requires_slug_confirmation(client, alice):
    """E-06: deletion revokes access for everyone in the team, so it is not a
    mis-click away."""
    team = _create(client, alice, "Alpha").json()

    wrong = client.delete(f"/api/teams/{team['id']}?confirm=nope", headers=alice)
    right = client.delete(
        f"/api/teams/{team['id']}?confirm={team['slug']}", headers=alice
    )

    assert wrong.status_code == 400
    assert right.status_code == 200


def test_delete_team_requires_owner(client, alice, bob):
    team = _create(client, alice, "Alpha").json()
    client.post(
        f"/api/teams/{team['id']}/members",
        json={"username": _username(client, bob), "role": "manager"},
        headers=alice,
    )

    res = client.delete(f"/api/teams/{team['id']}?confirm={team['slug']}", headers=bob)

    assert res.status_code == 403


def test_delete_team_cascades(client, alice, bob):
    """Grants and memberships go with the team, explicitly in application code
    rather than via a DB cascade SQLite only honours with a pragma (§ 4.4)."""
    team = _create(client, alice, "Alpha").json()
    client.post(
        f"/api/teams/{team['id']}/members",
        json={"username": _username(client, bob), "role": "member"},
        headers=alice,
    )
    project_id = client.post(
        "/api/projects",
        json={"name": "P", "slug": "p", "creator": "ignored"},
        headers=alice,
    ).json()["id"]

    db = SessionLocal()
    try:
        db.add(
            models.ProjectGrant(
                project_id=project_id, team_id=team["id"], role="annotator"
            )
        )
        db.commit()
    finally:
        db.close()

    res = client.delete(
        f"/api/teams/{team['id']}?confirm={team['slug']}", headers=alice
    )

    assert res.status_code == 200
    body = res.json()
    assert body["grants_removed"] == 1
    assert body["members_removed"] == 2

    db = SessionLocal()
    try:
        assert db.get(models.Team, team["id"]) is None
        assert (
            db.query(models.ProjectGrant)
            .filter(models.ProjectGrant.team_id == team["id"])
            .count()
            == 0
        )
        assert (
            db.query(models.TeamMembership)
            .filter(models.TeamMembership.team_id == team["id"])
            .count()
            == 0
        )
    finally:
        db.close()


def test_delete_team_nulls_task_assignment(client, alice):
    """The single most important cascade decision: deleting a team returns its
    tasks to the shared pool. CASCADE here would destroy annotated work."""
    team = _create(client, alice, "Alpha").json()
    project_id = client.post(
        "/api/projects",
        json={"name": "P", "slug": "p", "creator": "ignored"},
        headers=alice,
    ).json()["id"]

    db = SessionLocal()
    try:
        task = models.Task(
            project_id=project_id,
            image_path="a.jpg",
            status="New",
            annotations='[{"kind": "box"}]',
            assigned_team_id=team["id"],
        )
        db.add(task)
        db.commit()
        task_id = task.id
    finally:
        db.close()

    res = client.delete(
        f"/api/teams/{team['id']}?confirm={team['slug']}", headers=alice
    )

    assert res.json()["tasks_unassigned"] == 1

    db = SessionLocal()
    try:
        surviving = db.get(models.Task, task_id)
        assert surviving is not None, "deleting a team must never delete tasks"
        assert surviving.assigned_team_id is None
        assert surviving.annotations == '[{"kind": "box"}]'
    finally:
        db.close()


# --- GET /api/auth/me --------------------------------------------------------


def test_me_returns_identity_and_teams(client, alice):
    team_id = _create(client, alice, "Alpha").json()["id"]

    body = client.get("/api/auth/me", headers=alice).json()

    assert isinstance(body["id"], int)
    assert body["username"].startswith("alice-")
    assert body["teams"] == [{"id": team_id, "name": "Alpha", "role": "owner"}]


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_lists_teams_the_caller_only_belongs_to(client, alice, bob):
    _create(client, alice, "Alpha")
    bob_team = _create(client, bob, "Beta").json()["id"]

    body = client.get("/api/auth/me", headers=bob).json()

    assert [t["id"] for t in body["teams"]] == [bob_team]
