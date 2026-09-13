"""Project reviewers: appointment, the privileges granted, and their limits.

A reviewer is an annotator the project owner appoints to check other people's
work. The role is deliberately bounded: it widens `_is_task_editor` (edit any
task, including moving one out of a terminal status to send it back) and grants
project access, and nothing else. Destructive actions stay owner-only.

Two fixtures matter here. `is_project_creator` checks `owner_id == user.id`
unconditionally, so a request carrying the *owner's* token is the owner
whatever `X-Annotator-Name` it sends — a reviewer therefore has to be a
separate account (`bob`), not just a different annotator name on alice's token.
Getting this wrong makes every assertion below pass vacuously.
"""
import pytest


def _new_project(client, auth, name="rev-proj"):
    res = client.post(
        "/api/projects", json={"name": name, "slug": name, "creator": "ignored"}, headers=auth
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _new_task(client, auth, pid, description="a.png", assignee=None):
    res = client.post(
        "/api/tasks", params={"projectId": pid}, json={"description": description}, headers=auth
    )
    assert res.status_code == 200, res.text
    tid = res.json()["id"]
    if assignee:
        client.patch(f"/api/tasks/{tid}?projectId={pid}", json={"assignee": assignee}, headers=auth)
    return tid


def _member(client, auth, name):
    """Ensure a TeamMember row exists; get_current_annotator returns None without one."""
    client.post("/api/team/ping", headers={**auth, "X-Annotator-Name": name})
    return name


# --- appointment ------------------------------------------------------


def test_owner_can_appoint_and_list_reviewers(client, alice):
    pid = _new_project(client, alice)
    _member(client, alice, "Rev")

    res = client.post(f"/api/projects/{pid}/reviewers", json={"member_name": "Rev"}, headers=alice)
    assert res.status_code == 200, res.text
    assert res.json()["member_name"] == "Rev"

    listed = client.get(f"/api/projects/{pid}/reviewers", headers=alice).json()
    assert [r["member_name"] for r in listed] == ["Rev"]


def test_appointing_twice_is_idempotent(client, alice):
    """A double-click must not stack duplicate rows."""
    pid = _new_project(client, alice)
    _member(client, alice, "Rev")

    client.post(f"/api/projects/{pid}/reviewers", json={"member_name": "Rev"}, headers=alice)
    second = client.post(f"/api/projects/{pid}/reviewers", json={"member_name": "Rev"}, headers=alice)
    assert second.status_code == 200

    listed = client.get(f"/api/projects/{pid}/reviewers", headers=alice).json()
    assert len(listed) == 1


def test_cannot_appoint_a_non_member(client, alice):
    """A typo must not silently mint a member who never logs in."""
    pid = _new_project(client, alice)
    res = client.post(f"/api/projects/{pid}/reviewers", json={"member_name": "Ghost"}, headers=alice)
    assert res.status_code == 400
    assert "not a team member" in res.json()["detail"]


def test_owner_can_remove_a_reviewer(client, alice):
    pid = _new_project(client, alice)
    _member(client, alice, "Rev")
    client.post(f"/api/projects/{pid}/reviewers", json={"member_name": "Rev"}, headers=alice)

    res = client.delete(f"/api/projects/{pid}/reviewers/Rev", headers=alice)
    assert res.status_code == 200
    assert res.json()["removed"] == 1
    assert client.get(f"/api/projects/{pid}/reviewers", headers=alice).json() == []


def test_non_owner_cannot_appoint_or_remove(client, alice, bob):
    """Only the project creator appoints; the role must not spread on its own."""
    pid = _new_project(client, alice)
    rev = _member(client, alice, "Rev")
    client.post(f"/api/projects/{pid}/reviewers", json={"member_name": rev}, headers=alice)

    # bob is a separate account, appointed as a reviewer, acting as that annotator.
    bob_rev = {**bob, "X-Annotator-Name": rev}
    assert client.post(
        f"/api/projects/{pid}/reviewers", json={"member_name": rev}, headers=bob_rev
    ).status_code == 403
    assert client.delete(f"/api/projects/{pid}/reviewers/{rev}", headers=bob_rev).status_code == 403


# --- what the role grants ---------------------------------------------


def test_reviewer_gains_project_access(client, alice, bob):
    """A reviewer may hold no task and belong to no team, yet must see the project."""
    pid = _new_project(client, alice)
    rev = _member(client, alice, "Rev")

    bob_rev = {**bob, "X-Annotator-Name": rev}
    assert client.get(f"/api/projects/{pid}", headers=bob_rev).status_code == 404

    client.post(f"/api/projects/{pid}/reviewers", json={"member_name": rev}, headers=alice)

    got = client.get(f"/api/projects/{pid}", headers=bob_rev)
    assert got.status_code == 200
    body = got.json()
    assert body["is_reviewer"] is True
    assert body["is_owner"] is False
    assert body["reviewers"] == [rev]


def test_reviewer_can_edit_a_task_assigned_to_someone_else(client, alice, bob):
    pid = _new_project(client, alice)
    _member(client, alice, "Annot")
    rev = _member(client, alice, "Rev")
    tid = _new_task(client, alice, pid, assignee="Annot")

    bob_rev = {**bob, "X-Annotator-Name": rev}
    # Before appointment bob cannot even see the project.
    assert client.get(f"/api/tasks/{tid}", headers=bob_rev).status_code == 404

    client.post(f"/api/projects/{pid}/reviewers", json={"member_name": rev}, headers=alice)

    res = client.post(
        f"/api/tasks?projectId={pid}&taskId={tid}",
        json={"id": tid, "status": "Completed"}, headers=bob_rev,
    )
    assert res.status_code == 200, res.text
    assert client.get(f"/api/tasks/{tid}", headers=alice).json()["status"] == "Completed"


def test_reviewer_can_move_a_task_out_of_a_terminal_status(client, alice, bob):
    """Sending finished work back for rework is the core of the role."""
    pid = _new_project(client, alice)
    _member(client, alice, "Annot")
    rev = _member(client, alice, "Rev")
    tid = _new_task(client, alice, pid, assignee="Annot")
    client.post(f"/api/projects/{pid}/reviewers", json={"member_name": rev}, headers=alice)

    bob_rev = {**bob, "X-Annotator-Name": rev}
    client.post(f"/api/tasks?projectId={pid}&taskId={tid}",
                json={"id": tid, "status": "Verified"}, headers=bob_rev)
    assert client.get(f"/api/tasks/{tid}", headers=alice).json()["status"] == "Verified"

    client.post(f"/api/tasks?projectId={pid}&taskId={tid}",
                json={"id": tid, "status": "In Progress"}, headers=bob_rev)
    assert client.get(f"/api/tasks/{tid}", headers=alice).json()["status"] == "In Progress"


def test_a_stranger_still_cannot_unlock_a_terminal_status(client, alice, bob):
    """The status lock still holds for someone who is neither assignee nor reviewer."""
    pid = _new_project(client, alice)
    _member(client, alice, "Annot")
    other = _member(client, alice, "Other")
    tid = _new_task(client, alice, pid, assignee="Annot")
    client.post(f"/api/tasks?projectId={pid}&taskId={tid}",
                json={"id": tid, "status": "Verified"}, headers=alice)

    # `Other` reaches the project only by being given an assigned task in it.
    other_tid = _new_task(client, alice, pid, description="b.png", assignee=other)
    assert other_tid

    bob_other = {**bob, "X-Annotator-Name": other}
    client.post(f"/api/tasks?projectId={pid}&taskId={tid}",
                json={"id": tid, "status": "In Progress"}, headers=bob_other)
    # The rejected status change is dropped, not raised — the task stays put.
    assert client.get(f"/api/tasks/{tid}", headers=alice).json()["status"] == "Verified"


# --- what the role does NOT grant -------------------------------------


def test_reviewer_cannot_delete_a_task(client, alice, bob):
    """Destructive actions stay with the owner."""
    pid = _new_project(client, alice)
    rev = _member(client, alice, "Rev")
    tid = _new_task(client, alice, pid)
    client.post(f"/api/projects/{pid}/reviewers", json={"member_name": rev}, headers=alice)

    bob_rev = {**bob, "X-Annotator-Name": rev}
    assert client.delete(f"/api/tasks/{tid}?projectId={pid}", headers=bob_rev).status_code == 403
    assert client.get(f"/api/tasks/{tid}", headers=alice).status_code == 200


def test_reviewer_cannot_add_tasks_or_delete_the_project(client, alice, bob):
    pid = _new_project(client, alice)
    rev = _member(client, alice, "Rev")
    client.post(f"/api/projects/{pid}/reviewers", json={"member_name": rev}, headers=alice)

    bob_rev = {**bob, "X-Annotator-Name": rev}
    assert client.post(
        "/api/tasks", params={"projectId": pid}, json={"description": "new.png"}, headers=bob_rev
    ).status_code == 403
    assert client.delete(f"/api/projects/{pid}", headers=bob_rev).status_code == 403


def test_a_plain_teammate_cannot_delete_or_rename_a_project(client, alice, bob):
    """The wider bug the reviewer work exposed.

    `get_owned_project` answers "may this caller see it", which is true for any
    team member or task assignee. Both mutating project routes used to authorize
    with that alone, so any teammate could delete a project — and its tasks and
    labels with it. This pins the owner-only gate for a caller who reaches the
    project by holding an assigned task, with no reviewer row involved.
    """
    pid = _new_project(client, alice)
    mate = _member(client, alice, "Mate")
    _new_task(client, alice, pid, assignee=mate)

    bob_mate = {**bob, "X-Annotator-Name": mate}
    assert client.get(f"/api/projects/{pid}", headers=bob_mate).status_code == 200

    assert client.patch(
        f"/api/projects/{pid}", json={"name": "renamed"}, headers=bob_mate
    ).status_code == 403
    assert client.delete(f"/api/projects/{pid}", headers=bob_mate).status_code == 403
    assert client.get(f"/api/projects/{pid}", headers=alice).json()["name"] == "rev-proj"


def test_reviewer_role_is_scoped_to_one_project(client, alice, bob):
    """Appointment on one project grants nothing on another."""
    reviewed = _new_project(client, alice, "reviewed")
    other = _new_project(client, alice, "other")
    rev = _member(client, alice, "Rev")
    client.post(f"/api/projects/{reviewed}/reviewers", json={"member_name": rev}, headers=alice)

    bob_rev = {**bob, "X-Annotator-Name": rev}
    assert client.get(f"/api/projects/{reviewed}", headers=bob_rev).status_code == 200
    assert client.get(f"/api/projects/{other}", headers=bob_rev).status_code == 404


def test_removing_a_reviewer_revokes_the_access(client, alice, bob):
    pid = _new_project(client, alice)
    rev = _member(client, alice, "Rev")
    client.post(f"/api/projects/{pid}/reviewers", json={"member_name": rev}, headers=alice)

    bob_rev = {**bob, "X-Annotator-Name": rev}
    assert client.get(f"/api/projects/{pid}", headers=bob_rev).status_code == 200

    client.delete(f"/api/projects/{pid}/reviewers/{rev}", headers=alice)
    assert client.get(f"/api/projects/{pid}", headers=bob_rev).status_code == 404
