"""`/api/time-logs` — the renamed `team_members` table and its scoping fix.

Spec: .devnotes/teams/02_SCHEMA.md § 8, .devnotes/teams/01_DESIGN.md § 8,
TASKS.md T1.6.

The rename is behaviour-preserving by design; what genuinely changes is that
`GET` no longer returns every row in the table to every authenticated caller.
"""
import models
from database import SessionLocal


def _log_time(client, headers, seconds):
    return client.post("/api/time-logs/time", json={"name": "ignored", "time_logged": seconds}, headers=headers)


def test_rename_preserves_time_logged(client, alice):
    """Seconds accumulate across calls exactly as before the rename."""
    first = _log_time(client, alice, 30)
    second = _log_time(client, alice, 45)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["time_logged"] == 75


def test_time_is_credited_to_the_authenticated_user(client, alice):
    """The client-supplied `name` is ignored — it came from editable
    localStorage and let anyone log time against anyone (docs/TIMER_AUDIT.md F7).
    """
    _log_time(client, alice, 60)

    rows = client.get("/api/time-logs", headers=alice).json()
    names = {row["name"] for row in rows}

    assert names, "the caller must see their own row"
    assert "ignored" not in names


def test_get_time_logs_scoped_to_caller(client, alice, bob):
    """The pre-existing leak: GET used to return every row to any caller.

    Alice logging time must not make her row visible to Bob, who is in no team
    with her.
    """
    _log_time(client, alice, 120)
    _log_time(client, bob, 15)

    alice_rows = client.get("/api/time-logs", headers=alice).json()
    bob_rows = client.get("/api/time-logs", headers=bob).json()

    assert len(alice_rows) == 1
    assert len(bob_rows) == 1
    assert alice_rows[0]["time_logged"] == 120
    assert bob_rows[0]["time_logged"] == 15
    assert alice_rows[0]["name"] != bob_rows[0]["name"]


def test_time_log_links_to_the_user_account(client, alice):
    """`user_id` is populated for the authenticated writer, which is what turns
    this table into real per-person attribution under individual accounts."""
    _log_time(client, alice, 10)

    rows = client.get("/api/time-logs", headers=alice).json()

    assert rows[0]["user_id"] is not None


def test_team_manager_sees_their_members_rows(client, alice, bob):
    """A team manager may see their members' logged time — and only theirs."""
    _log_time(client, alice, 100)
    _log_time(client, bob, 200)

    alice_name = client.get("/api/time-logs", headers=alice).json()[0]["name"]
    bob_name = client.get("/api/time-logs", headers=bob).json()[0]["name"]

    db = SessionLocal()
    try:
        alice_user = db.query(models.User).filter(models.User.username == alice_name).one()
        bob_user = db.query(models.User).filter(models.User.username == bob_name).one()
        team = models.Team(name="qa", slug=f"qa-{alice_user.id}", owner_id=alice_user.id)
        db.add(team)
        db.flush()
        db.add(models.TeamMembership(team_id=team.id, user_id=alice_user.id, role="owner"))
        db.add(models.TeamMembership(team_id=team.id, user_id=bob_user.id, role="member"))
        db.commit()
    finally:
        db.close()

    alice_rows = client.get("/api/time-logs", headers=alice).json()
    bob_rows = client.get("/api/time-logs", headers=bob).json()

    assert {row["name"] for row in alice_rows} == {alice_name, bob_name}
    # The member does not gain sight of the manager's row in return.
    assert {row["name"] for row in bob_rows} == {bob_name}


def test_legacy_team_route_still_works(client, alice):
    """`/api/team` stays mounted for one release: annotators may be running a
    cached JS bundle that still calls it (deferred item D4)."""
    posted = client.post(
        "/api/team/time", json={"name": "ignored", "time_logged": 25}, headers=alice
    )
    listed = client.get("/api/team", headers=alice)

    assert posted.status_code == 200, posted.text
    assert posted.json()["time_logged"] == 25
    assert listed.status_code == 200
    assert listed.json()[0]["time_logged"] == 25


def test_legacy_route_shares_the_scoping_fix(client, alice, bob):
    """The alias delegates rather than duplicating, so the leak is closed on
    both paths — a second copy of the handler would have re-opened it."""
    _log_time(client, alice, 40)
    _log_time(client, bob, 50)

    assert len(client.get("/api/team", headers=alice).json()) == 1
    assert len(client.get("/api/team", headers=bob).json()) == 1


def test_backfill_matches_exact_username_only(client, alice):
    """A free-text row that matches no account keeps `user_id = NULL`.

    Unmatched historical names are never guessed at: a fuzzy match would be a
    claim about who did the work (.devnotes/teams/05_MIGRATION.md § 5).
    """
    created = client.post(
        "/api/time-logs", json={"name": "not-a-real-account"}, headers=alice
    )

    assert created.status_code == 200, created.text
    assert created.json()["user_id"] is None


def test_create_links_an_exact_username_match(client, alice):
    """The same exact-match rule the M4 backfill uses, applied to new rows."""
    _log_time(client, alice, 5)
    alice_rows_name = client.get("/api/time-logs", headers=alice).json()[0]["name"]

    db = SessionLocal()
    try:
        db.query(models.TimeLog).filter(models.TimeLog.name == alice_rows_name).delete()
        db.commit()
    finally:
        db.close()

    created = client.post("/api/time-logs", json={"name": alice_rows_name}, headers=alice)

    assert created.status_code == 200, created.text
    assert created.json()["user_id"] is not None


def test_time_logs_requires_auth(client):
    assert client.get("/api/time-logs").status_code == 401
