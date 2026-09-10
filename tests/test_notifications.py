"""Notification emit + read behaviour.

The feature addresses *annotators* (`team_members.name`), not user accounts:
the LAN deployment shares one login, so a username cannot tell two people
apart. These tests pin that, plus the two rules that keep the bell honest —
notifications are written only after the described change commits, and an
autosave that merely echoes the current status/assignee emits nothing.
"""
import uuid

import pytest

from database import SessionLocal
import models


def _member(db, name):
    db.add(models.TeamMember(name=name, time_logged=0))
    db.commit()
    return name


def _new_project(client, auth, creator, name="proj"):
    res = client.post("/api/projects", json={"name": name, "slug": name, "creator": creator}, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _new_task(client, auth, project_id, description="img.png", assignee=None):
    body = {"description": description, "status": "New"}
    if assignee:
        body["assignee"] = assignee
    res = client.post(f"/api/tasks?projectId={project_id}", json=body, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


@pytest.fixture
def owner_name():
    """An annotator row for the account holder, as ping_presence would create."""
    name = f"owner_{uuid.uuid4().hex[:6]}"
    db = SessionLocal()
    try:
        _member(db, name)
    finally:
        db.close()
    return name


@pytest.fixture
def worker_name():
    name = f"worker_{uuid.uuid4().hex[:6]}"
    db = SessionLocal()
    try:
        _member(db, name)
    finally:
        db.close()
    return name


def test_assigning_a_task_notifies_the_assignee(client, alice, owner_name, worker_name):
    """The core 'new task assigned' case."""
    auth = {**alice, "X-Annotator-Name": owner_name}
    pid = _new_project(client, auth, owner_name)
    _new_task(client, auth, pid, "bird.png", assignee=worker_name)

    worker = {**alice, "X-Annotator-Name": worker_name}
    body = client.get("/api/notifications", headers=worker).json()
    assert len(body) == 1
    assert "bird.png" in body[0]["message"]
    assert body[0]["type"] == "task"
    assert body[0]["project_id"] == pid


def test_notification_names_the_project_and_carries_its_link_fields(client, alice, owner_name, worker_name):
    """The recipient must be able to tell which project a filename belongs to.

    The name is in the message *and* returned as `project_name`: the message is
    a fixed snapshot, while the field is resolved at read time so a renamed
    project shows its current name. `project_id` + `entity_id` are what the bell
    builds the "Open task" link from.
    """
    auth = {**alice, "X-Annotator-Name": owner_name}
    pid = _new_project(client, auth, owner_name, name="Aerial Survey")
    tid = _new_task(client, auth, pid, "field.png", assignee=worker_name)

    worker = {**alice, "X-Annotator-Name": worker_name}
    body = client.get("/api/notifications", headers=worker).json()
    assert len(body) == 1
    notice = body[0]

    assert "Aerial Survey" in notice["message"]
    assert notice["project_name"] == "Aerial Survey"
    # Both halves of the task link.
    assert notice["project_id"] == pid
    assert notice["entity_id"] == tid


def test_project_name_is_resolved_fresh_after_a_rename(client, alice, owner_name, worker_name):
    """A renamed project reports its new name, even though the message is fixed."""
    auth = {**alice, "X-Annotator-Name": owner_name}
    pid = _new_project(client, auth, owner_name, name="Old Name")
    _new_task(client, auth, pid, "x.png", assignee=worker_name)

    res = client.patch(f"/api/projects/{pid}", json={"name": "New Name"}, headers=auth)
    assert res.status_code == 200, res.text

    worker = {**alice, "X-Annotator-Name": worker_name}
    notice = client.get("/api/notifications", headers=worker).json()[0]
    assert notice["project_name"] == "New Name"
    # The stored message keeps the name it was written with.
    assert "Old Name" in notice["message"]


def test_status_change_notification_names_the_project(client, alice, owner_name, worker_name):
    auth = {**alice, "X-Annotator-Name": owner_name}
    pid = _new_project(client, auth, owner_name, name="Rooftops")
    tid = _new_task(client, auth, pid, "roof.png", assignee=worker_name)

    worker = {**alice, "X-Annotator-Name": worker_name}
    res = client.post(
        f"/api/tasks?projectId={pid}",
        json={"id": tid, "status": "Completed"},
        headers=worker,
    )
    assert res.status_code == 200, res.text

    notice = client.get("/api/notifications", headers=auth).json()[0]
    assert "Rooftops" in notice["message"]
    assert notice["project_name"] == "Rooftops"
    assert notice["project_id"] == pid


def test_bulk_assign_notifications_name_the_project(client, alice, owner_name, worker_name):
    auth = {**alice, "X-Annotator-Name": owner_name}
    pid = _new_project(client, auth, owner_name, name="Coastline")
    ids = [_new_task(client, auth, pid, f"c{i}.png") for i in range(2)]

    res = client.post(
        "/api/tasks/bulk-update",
        json={"ids": ids, "assignee": worker_name},
        headers=auth,
    )
    assert res.status_code == 200, res.text

    worker = {**alice, "X-Annotator-Name": worker_name}
    body = client.get("/api/notifications", headers=worker).json()
    assert len(body) == 2
    assert all("Coastline" in n["message"] for n in body)
    assert all(n["project_name"] == "Coastline" for n in body)


def test_status_change_notifies_the_project_owner(client, alice, owner_name, worker_name):
    auth = {**alice, "X-Annotator-Name": owner_name}
    pid = _new_project(client, auth, owner_name)
    tid = _new_task(client, auth, pid, "cat.png", assignee=worker_name)

    worker = {**alice, "X-Annotator-Name": worker_name}
    res = client.post(
        f"/api/tasks?projectId={pid}",
        json={"id": tid, "status": "Completed"},
        headers=worker,
    )
    assert res.status_code == 200, res.text

    owner_inbox = client.get("/api/notifications", headers=auth).json()
    assert any("Completed" in n["message"] for n in owner_inbox)


def test_autosave_echoing_the_same_status_emits_nothing(client, alice, owner_name, worker_name):
    """Every autosave resends the current status; only a real transition is news.

    Without this the owner's bell would gain an entry on each 30s drain.
    """
    auth = {**alice, "X-Annotator-Name": owner_name}
    pid = _new_project(client, auth, owner_name)
    tid = _new_task(client, auth, pid, "dog.png", assignee=worker_name)

    worker = {**alice, "X-Annotator-Name": worker_name}
    for _ in range(3):
        res = client.post(
            f"/api/tasks?projectId={pid}",
            json={"id": tid, "status": "New"},  # unchanged
            headers=worker,
        )
        assert res.status_code == 200, res.text

    assert client.get("/api/notifications", headers=auth).json() == []


def test_actor_is_not_notified_about_their_own_action(client, alice, owner_name):
    """The owner completing their own task should not notify themselves."""
    auth = {**alice, "X-Annotator-Name": owner_name}
    pid = _new_project(client, auth, owner_name)
    tid = _new_task(client, auth, pid, "self.png")

    res = client.post(
        f"/api/tasks?projectId={pid}",
        json={"id": tid, "status": "Completed"},
        headers=auth,
    )
    assert res.status_code == 200, res.text
    assert client.get("/api/notifications", headers=auth).json() == []


def test_notifications_are_scoped_to_the_caller(client, alice, owner_name, worker_name):
    """One annotator must never see another's notices."""
    auth = {**alice, "X-Annotator-Name": owner_name}
    pid = _new_project(client, auth, owner_name)
    _new_task(client, auth, pid, "scoped.png", assignee=worker_name)

    # The notice belongs to the worker, so the owner's inbox stays empty.
    assert client.get("/api/notifications", headers=auth).json() == []
    worker = {**alice, "X-Annotator-Name": worker_name}
    assert len(client.get("/api/notifications", headers=worker).json()) == 1


def test_mark_read_clears_only_the_callers_rows(client, alice, owner_name, worker_name):
    auth = {**alice, "X-Annotator-Name": owner_name}
    pid = _new_project(client, auth, owner_name)
    _new_task(client, auth, pid, "one.png", assignee=worker_name)

    worker = {**alice, "X-Annotator-Name": worker_name}
    unread = client.get("/api/notifications", headers=worker).json()
    assert len(unread) == 1
    notif_id = unread[0]["id"]

    # The owner cannot clear the worker's notice by guessing its id.
    res = client.post("/api/notifications/mark-read", json={"notification_ids": [notif_id]}, headers=auth)
    assert res.status_code == 200
    assert res.json()["updated"] == 0
    assert len(client.get("/api/notifications", headers=worker).json()) == 1

    # The owner of the row can.
    res = client.post("/api/notifications/mark-read", json={"notification_ids": [notif_id]}, headers=worker)
    assert res.status_code == 200
    assert res.json()["updated"] == 1
    assert client.get("/api/notifications", headers=worker).json() == []


def test_mark_read_with_empty_list_marks_everything(client, alice, owner_name, worker_name):
    auth = {**alice, "X-Annotator-Name": owner_name}
    pid = _new_project(client, auth, owner_name)
    _new_task(client, auth, pid, "a.png", assignee=worker_name)
    _new_task(client, auth, pid, "b.png", assignee=worker_name)

    worker = {**alice, "X-Annotator-Name": worker_name}
    assert len(client.get("/api/notifications", headers=worker).json()) == 2

    res = client.post("/api/notifications/mark-read", json={"notification_ids": []}, headers=worker)
    assert res.status_code == 200
    assert res.json()["updated"] == 2
    assert client.get("/api/notifications", headers=worker).json() == []


def test_bulk_assign_notifies_each_newly_assigned_task(client, alice, owner_name, worker_name):
    auth = {**alice, "X-Annotator-Name": owner_name}
    pid = _new_project(client, auth, owner_name)
    ids = [_new_task(client, auth, pid, f"bulk{i}.png") for i in range(3)]

    res = client.post(
        "/api/tasks/bulk-update",
        json={"ids": ids, "assignee": worker_name},
        headers=auth,
    )
    assert res.status_code == 200, res.text

    worker = {**alice, "X-Annotator-Name": worker_name}
    assert len(client.get("/api/notifications", headers=worker).json()) == 3


def test_reassigning_to_the_same_person_is_not_news(client, alice, owner_name, worker_name):
    auth = {**alice, "X-Annotator-Name": owner_name}
    pid = _new_project(client, auth, owner_name)
    ids = [_new_task(client, auth, pid, "again.png", assignee=worker_name)]

    worker = {**alice, "X-Annotator-Name": worker_name}
    before = len(client.get("/api/notifications", headers=worker).json())

    res = client.post(
        "/api/tasks/bulk-update",
        json={"ids": ids, "assignee": worker_name},  # already theirs
        headers=auth,
    )
    assert res.status_code == 200, res.text
    assert len(client.get("/api/notifications", headers=worker).json()) == before


def test_notification_for_unknown_member_is_skipped_not_fatal(client, alice, owner_name):
    """An assignee with no team_members row must not 500 the save.

    The FK would reject the row, so `emit` skips it; the task write itself has
    already committed and must still succeed.
    """
    auth = {**alice, "X-Annotator-Name": owner_name}
    pid = _new_project(client, auth, owner_name)
    res = client.post(
        f"/api/tasks?projectId={pid}",
        json={"description": "ghost.png", "status": "New", "assignee": "no-such-member"},
        headers=auth,
    )
    assert res.status_code == 200, res.text
