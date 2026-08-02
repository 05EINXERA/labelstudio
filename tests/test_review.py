"""The review flow — approve, reject, reopen.

Spec: .devnotes/teams/03_API.md § 4.3, 01_DESIGN.md § 4. Edge cases: E-11, E-28.

The behaviour change that matters most here: approving now requires the Reviewer
role. Under a shared account the single user owns everything, so the owner
short-circuit still lets them approve — it only bites once individual accounts
exist, which is the point.
"""
import models
from database import SessionLocal
from formats.common import from_external_status, to_external_status


def _me(client, headers):
    return client.get("/api/auth/me", headers=headers).json()


def _project(client, headers, name="P"):
    return client.post(
        "/api/projects",
        json={"name": name, "slug": name.lower(), "creator": "ignored"},
        headers=headers,
    ).json()["id"]


def _task(client, headers, project_id, status="Completed"):
    return client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": "a.jpg", "status": status},
        headers=headers,
    ).json()["id"]


def _member_with_role(client, owner, other, project_id, role):
    """Put `other` on `project_id` with the given grant role, return their name."""
    team = client.post(
        "/api/teams", json={"name": f"T-{role}-{project_id}"}, headers=owner
    ).json()
    name = _me(client, other)["username"]
    client.post(
        f"/api/teams/{team['id']}/members",
        json={"username": name, "role": "member"},
        headers=owner,
    )
    client.post(
        f"/api/projects/{project_id}/grants",
        json={"team_id": team["id"], "role": role},
        headers=owner,
    )
    return team


# --- the reviewer gate -------------------------------------------------------


def test_annotator_cannot_approve(client, alice, bob):
    project_id = _project(client, alice)
    _member_with_role(client, alice, bob, project_id, "annotator")
    task_id = _task(client, alice, project_id)

    via_patch = client.post(
        "/api/tasks", json={"id": task_id, "status": "Approved"}, headers=bob
    )
    via_verb = client.post(
        f"/api/tasks/{task_id}/review", json={"action": "approved"}, headers=bob
    )

    assert via_patch.status_code == 403
    assert "Reviewer" in via_patch.json()["detail"]
    assert via_verb.status_code == 403


def test_annotator_can_still_do_ordinary_work(client, alice, bob):
    """The gate covers review transitions only. New → In Progress → Completed is
    ordinary annotation and must stay untouched — this is what keeps the shared
    deployment behaving identically."""
    project_id = _project(client, alice)
    _member_with_role(client, alice, bob, project_id, "annotator")
    task_id = _task(client, alice, project_id, status="New")

    res = client.post(
        "/api/tasks", json={"id": task_id, "status": "Completed"}, headers=bob
    )

    assert res.status_code == 200


def test_reviewer_can_approve(client, alice, bob):
    project_id = _project(client, alice)
    _member_with_role(client, alice, bob, project_id, "reviewer")
    task_id = _task(client, alice, project_id)

    res = client.post(
        f"/api/tasks/{task_id}/review", json={"action": "approved"}, headers=bob
    )

    assert res.status_code == 200, res.text
    assert res.json()["task_status"] == "Approved"


def test_owner_can_approve(client, alice):
    """The shared-account path: the single user owns everything, so the owner
    short-circuit carries them through unchanged."""
    project_id = _project(client, alice)
    task_id = _task(client, alice, project_id)

    res = client.post(
        f"/api/tasks/{task_id}/review", json={"action": "approved"}, headers=alice
    )

    assert res.status_code == 200
    assert res.json()["task_status"] == "Approved"


def test_reopen_from_approved_requires_reviewer(client, alice, bob):
    """Un-approving is exactly as privileged as approving: otherwise an
    annotator could quietly undo a review decision."""
    project_id = _project(client, alice)
    _member_with_role(client, alice, bob, project_id, "annotator")
    task_id = _task(client, alice, project_id)
    client.post(
        f"/api/tasks/{task_id}/review", json={"action": "approved"}, headers=alice
    )

    res = client.post(
        "/api/tasks", json={"id": task_id, "status": "In Progress"}, headers=bob
    )

    assert res.status_code == 403


def test_bulk_update_status_respects_reviewer_gate(client, alice, bob):
    """E-11: the obvious bypass. Without this, an annotator approves in batch
    what they cannot approve one at a time."""
    project_id = _project(client, alice)
    _member_with_role(client, alice, bob, project_id, "annotator")
    ids = [_task(client, alice, project_id) for _ in range(3)]

    res = client.post(
        "/api/tasks/bulk-update",
        json={"ids": ids, "status": "Approved"},
        headers=bob,
    )

    assert res.status_code == 200
    assert res.json()["updated"] == 0
    assert res.json()["skipped"] == 3

    db = SessionLocal()
    try:
        statuses = {
            db.get(models.Task, tid).status for tid in ids
        }
        assert statuses == {"Completed"}, "no task may have been approved"
    finally:
        db.close()


def test_bulk_update_allows_a_reviewer(client, alice, bob):
    project_id = _project(client, alice)
    _member_with_role(client, alice, bob, project_id, "reviewer")
    ids = [_task(client, alice, project_id) for _ in range(2)]

    res = client.post(
        "/api/tasks/bulk-update",
        json={"ids": ids, "status": "Approved"},
        headers=bob,
    )

    assert res.json()["updated"] == 2
    assert res.json()["skipped"] == 0


def test_bulk_update_non_review_status_still_needs_manager(client, alice, bob):
    """A reviewer is not automatically a bulk editor: the ordinary bulk minimum
    stays `manager` for non-review fields."""
    project_id = _project(client, alice)
    _member_with_role(client, alice, bob, project_id, "reviewer")
    ids = [_task(client, alice, project_id)]

    res = client.post(
        "/api/tasks/bulk-update",
        json={"ids": ids, "status": "In Progress"},
        headers=bob,
    )

    assert res.json()["skipped"] == 1


def test_reviewer_cannot_smuggle_an_assignee_change_into_a_bulk_approve(client, alice, bob):
    """The reviewer minimum applies to a *status-only* bulk update.

    Bundling `assignee` into the same request is an administrative bulk edit and
    falls back to the manager minimum — otherwise the reviewer gate would double
    as a way to reassign work in bulk.
    """
    project_id = _project(client, alice)
    _member_with_role(client, alice, bob, project_id, "reviewer")
    ids = [_task(client, alice, project_id)]

    res = client.post(
        "/api/tasks/bulk-update",
        json={"ids": ids, "status": "Approved", "assignee": "someone-else"},
        headers=bob,
    )

    assert res.json()["skipped"] == 1
    assert res.json()["updated"] == 0


# --- the audit trail ---------------------------------------------------------


def test_review_row_written_atomically(client, alice):
    """The TaskReview row and the status change share one commit. A review
    without its status change would make the audit trail lie."""
    project_id = _project(client, alice)
    task_id = _task(client, alice, project_id)

    client.post(
        f"/api/tasks/{task_id}/review",
        json={"action": "approved", "note": "looks good"},
        headers=alice,
    )

    db = SessionLocal()
    try:
        task = db.get(models.Task, task_id)
        reviews = (
            db.query(models.TaskReview)
            .filter(models.TaskReview.task_id == task_id)
            .all()
        )
        assert task.status == "Approved"
        assert len(reviews) == 1
        assert reviews[0].action == "approved"
        assert reviews[0].previous_status == "Completed"
    finally:
        db.close()


def test_reject_records_note(client, alice):
    project_id = _project(client, alice)
    task_id = _task(client, alice, project_id)

    res = client.post(
        f"/api/tasks/{task_id}/review",
        json={"action": "rejected", "note": "boxes are off by a few pixels"},
        headers=alice,
    )

    assert res.json()["task_status"] == "Rejected"
    assert res.json()["review"]["note"] == "boxes are off by a few pixels"


def test_reopen_returns_the_task_to_in_progress(client, alice):
    project_id = _project(client, alice)
    task_id = _task(client, alice, project_id)
    client.post(
        f"/api/tasks/{task_id}/review", json={"action": "approved"}, headers=alice
    )

    res = client.post(
        f"/api/tasks/{task_id}/review", json={"action": "reopened"}, headers=alice
    )

    assert res.json()["task_status"] == "In Progress"
    assert res.json()["review"]["previous_status"] == "Approved"


def test_review_history_is_append_only_and_newest_first(client, alice):
    project_id = _project(client, alice)
    task_id = _task(client, alice, project_id)
    for action in ("approved", "reopened", "rejected"):
        client.post(
            f"/api/tasks/{task_id}/review", json={"action": action}, headers=alice
        )

    history = client.get(f"/api/tasks/{task_id}/reviews", headers=alice).json()

    assert [r["action"] for r in history] == ["rejected", "reopened", "approved"]
    assert history[0]["reviewer_username"].startswith("alice-")


def test_review_history_readable_by_a_viewer(client, alice, bob):
    project_id = _project(client, alice)
    _member_with_role(client, alice, bob, project_id, "viewer")
    task_id = _task(client, alice, project_id)
    client.post(
        f"/api/tasks/{task_id}/review", json={"action": "approved"}, headers=alice
    )

    res = client.get(f"/api/tasks/{task_id}/reviews", headers=bob)

    assert res.status_code == 200
    assert len(res.json()) == 1


def test_self_approval_is_allowed_but_recorded(client, alice):
    """01_DESIGN.md § 4.1: blocking self-approval breaks the small-team case and
    is trivially defeated. Making it visible beats making it impossible."""
    project_id = _project(client, alice)
    task_id = _task(client, alice, project_id)

    res = client.post(
        f"/api/tasks/{task_id}/review", json={"action": "approved"}, headers=alice
    )

    assert res.status_code == 200
    assert res.json()["review"]["reviewer_username"].startswith("alice-")


# --- E-28: the Rejected status in the export/import vocabulary ---------------


def test_rejected_status_exported(client, alice):
    """`Rejected` must be a valid export filter value, not a 422."""
    project_id = _project(client, alice)
    task_id = _task(client, alice, project_id)
    client.post(
        f"/api/tasks/{task_id}/review", json={"action": "rejected"}, headers=alice
    )

    res = client.post(
        "/api/exports",
        json={"projectId": project_id, "format": "coco", "statusFilter": ["Rejected"]},
        headers=alice,
    )

    assert res.status_code in (200, 202), res.text


def test_rejected_status_round_trips_through_the_interop_vocabulary():
    """E-28: `from_external_status` previously special-cased only "approved", so
    an imported rejection silently became "In Progress" — the base status
    "Rejected" shares with ordinary unfinished work."""
    status, external = to_external_status("Rejected")

    assert (status, external) == ("in_progress", "rejected")
    assert from_external_status(status, external) == "Rejected"


def test_approved_still_round_trips():
    status, external = to_external_status("Approved")

    assert from_external_status(status, external) == "Approved"


def test_unreviewed_in_progress_is_unaffected():
    """A task that is merely unfinished must not be read back as rejected."""
    status, external = to_external_status("In Progress")

    assert external == ""
    assert from_external_status(status, external) == "In Progress"
