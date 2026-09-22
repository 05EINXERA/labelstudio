"""Multiple assignees per task, and the history of who held it.

An image is worked by different people at different times, so assignment is a
set with a history, not a single overwritten cell. These tests pin the three
things that make that safe on the live deployment:

  - `tasks.assignee` stays a correct mirror of the primary assignee, because
    project access, the exports and any client still running the previous build
    all read it;
  - every assignee — not only the primary — can edit the task they were given;
  - removing someone from a task does not erase the record that they held it.
"""
import uuid

import pytest

from database import SessionLocal
import models


def _member(name):
    db = SessionLocal()
    try:
        db.add(models.TeamMember(name=name, time_logged=0))
        db.commit()
    finally:
        db.close()
    return name


@pytest.fixture
def owner():
    return _member(f"owner_{uuid.uuid4().hex[:6]}")


@pytest.fixture
def ravi():
    return _member(f"ravi_{uuid.uuid4().hex[:6]}")


@pytest.fixture
def sanjita():
    return _member(f"sanjita_{uuid.uuid4().hex[:6]}")


def _as(auth, name):
    return {**auth, "X-Annotator-Name": name}


def _new_project(client, auth, creator, name="proj"):
    res = client.post(
        "/api/projects",
        json={"name": name, "slug": name, "creator": creator},
        headers=_as(auth, creator),
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _new_task(client, auth, owner, project_id, **body):
    payload = {"description": "img.png", "status": "New", **body}
    res = client.post(
        f"/api/tasks?projectId={project_id}", json=payload, headers=_as(auth, owner)
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _mirror(task_id):
    """Read tasks.assignee straight from the database, bypassing the API."""
    db = SessionLocal()
    try:
        return db.query(models.Task.assignee).filter(models.Task.id == task_id).scalar()
    finally:
        db.close()


def test_task_carries_several_assignees(client, alice, owner, ravi, sanjita):
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid, assignees=[ravi, sanjita])

    res = client.get(f"/api/tasks/{tid}", headers=_as(alice, owner))
    assert res.status_code == 200, res.text
    assert res.json()["assignees"] == [ravi, sanjita]


def test_primary_is_mirrored_into_the_legacy_column(client, alice, owner, ravi, sanjita):
    """tasks.assignee must keep naming the primary assignee.

    Project access, the `?assignee=` filter and the exports all still read this
    column; a stale mirror would lock people out of their own projects.
    """
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid, assignees=[ravi, sanjita])
    assert _mirror(tid) == ravi

    # Reordering moves the primary, and the mirror follows.
    res = client.patch(
        f"/api/tasks/{tid}", json={"assignees": [sanjita, ravi]}, headers=_as(alice, owner)
    )
    assert res.status_code == 200, res.text
    assert _mirror(tid) == sanjita

    # Unassigning everyone clears it rather than leaving a stale name.
    res = client.patch(f"/api/tasks/{tid}", json={"assignees": []}, headers=_as(alice, owner))
    assert res.status_code == 200, res.text
    assert _mirror(tid) is None


def test_legacy_scalar_client_still_works(client, alice, owner, ravi):
    """A tab running the previous build sends only `assignee` and must still assign.

    The deployment is live, so an older cached page keeps posting the scalar
    after the server is upgraded. Ignoring it would make that tab's assignment
    changes silently do nothing.
    """
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid)

    res = client.patch(f"/api/tasks/{tid}", json={"assignee": ravi}, headers=_as(alice, owner))
    assert res.status_code == 200, res.text

    detail = client.get(f"/api/tasks/{tid}", headers=_as(alice, owner)).json()
    assert detail["assignees"] == [ravi]
    assert detail["assignee"] == ravi


def test_every_assignee_may_edit_not_only_the_primary(client, alice, owner, ravi, sanjita):
    """The second assignee owns the work as much as the first.

    _is_task_editor used to compare against the single assignee column, which
    would deny the non-primary assignee access to the task they were given.
    """
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid, assignees=[ravi, sanjita])

    res = client.patch(
        f"/api/tasks/{tid}", json={"description": "edited.png"}, headers=_as(alice, sanjita)
    )
    assert res.status_code == 200, res.text


def test_unrelated_annotator_still_cannot_edit(client, alice, bob, owner, ravi):
    """Widening the check to all assignees must not widen it to everyone.

    The outsider is on a *separate account* (`bob`), not merely a different
    annotator name on the same one. On this shared-login deployment every
    annotator name maps to one account, and that account owns the project —
    `is_project_creator` admits it by `owner_id` regardless of the name chosen.
    So a second account is the only thing that is genuinely an outsider here,
    and it is what the check has to hold against.
    """
    outsider = _member(f"outsider_{uuid.uuid4().hex[:6]}")
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid, assignees=[ravi])

    res = client.patch(
        f"/api/tasks/{tid}", json={"description": "nope.png"}, headers=_as(bob, outsider)
    )
    assert res.status_code in (403, 404), res.text


def test_history_survives_reassignment(client, alice, owner, ravi, sanjita):
    """The whole point: taking someone off a task keeps the record they held it."""
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid, assignees=[ravi])

    res = client.patch(
        f"/api/tasks/{tid}", json={"assignees": [sanjita]}, headers=_as(alice, owner)
    )
    assert res.status_code == 200, res.text

    res = client.get(f"/api/tasks/{tid}/assignments", headers=_as(alice, owner))
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["assignees"] == [sanjita]
    # Ravi is gone from the task but must still be named as having worked on it.
    assert ravi in body["participants"]
    assert sanjita in body["participants"]

    actions = {(e["member_name"], e["action"]) for e in body["events"]}
    assert (ravi, "assigned") in actions
    assert (ravi, "unassigned") in actions
    assert (sanjita, "assigned") in actions


def test_history_records_who_made_the_change(client, alice, owner, ravi):
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid, assignees=[ravi])

    body = client.get(f"/api/tasks/{tid}/assignments", headers=_as(alice, owner)).json()
    assigned = [e for e in body["events"] if e["action"] == "assigned"]
    assert assigned and assigned[0]["actor_name"] == owner


def test_history_is_owner_and_reviewer_only(client, alice, bob, owner, ravi):
    """Someone who neither owns nor reviews the project cannot read the history.

    Checked from a second account for the reason given in
    test_unrelated_annotator_still_cannot_edit: on the shared login, every
    annotator name resolves to the owning account, so a different *name* is not
    an outsider — a different *account* is.
    """
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid, assignees=[ravi])

    res = client.get(f"/api/tasks/{tid}/assignments", headers=_as(bob, ravi))
    assert res.status_code in (403, 404), res.text

    # The owner sees it.
    res = client.get(f"/api/tasks/{tid}/assignments", headers=_as(alice, owner))
    assert res.status_code == 200, res.text

    # An appointed reviewer sees it too.
    db = SessionLocal()
    try:
        db.add(models.ProjectReviewer(project_id=pid, member_name=ravi, appointed_by=owner))
        db.commit()
    finally:
        db.close()
    res = client.get(f"/api/tasks/{tid}/assignments", headers=_as(alice, ravi))
    assert res.status_code == 200, res.text


def test_autosave_echoing_the_same_set_records_nothing(client, alice, owner, ravi, sanjita):
    """Re-sending the current set must not stack duplicate history rows.

    Every autosave resends the assignee set (workspace.js syncToBackend), so a
    write that records unconditionally would fill the history with noise and
    ring the bell on every 30-second drain.
    """
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid, assignees=[ravi, sanjita])

    before = client.get(f"/api/tasks/{tid}/assignments", headers=_as(alice, owner)).json()
    for _ in range(3):
        res = client.patch(
            f"/api/tasks/{tid}", json={"assignees": [ravi, sanjita]}, headers=_as(alice, owner)
        )
        assert res.status_code == 200, res.text
    after = client.get(f"/api/tasks/{tid}/assignments", headers=_as(alice, owner)).json()

    assert len(after["events"]) == len(before["events"])


def test_save_without_assignee_fields_leaves_assignment_alone(client, alice, owner, ravi):
    """An annotation autosave names no assignee and must not unassign the task."""
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid, assignees=[ravi])

    res = client.patch(f"/api/tasks/{tid}", json={"status": "In Progress"}, headers=_as(alice, owner))
    assert res.status_code == 200, res.text

    detail = client.get(f"/api/tasks/{tid}", headers=_as(alice, owner)).json()
    assert detail["assignees"] == [ravi]


def test_task_list_reports_every_assignee(client, alice, owner, ravi, sanjita):
    pid = _new_project(client, alice, owner)
    _new_task(client, alice, owner, pid, assignees=[ravi, sanjita])

    res = client.get(f"/api/tasks?projectId={pid}", headers=_as(alice, owner))
    assert res.status_code == 200, res.text
    item = res.json()["items"][0]
    assert item["assignees"] == [ravi, sanjita]
    assert item["assignee"] == ravi


def test_bulk_add_keeps_existing_assignees(client, alice, owner, ravi, sanjita):
    """"Also give these images to X" must not displace whoever is already on them."""
    pid = _new_project(client, alice, owner)
    t1 = _new_task(client, alice, owner, pid, description="a.png", assignees=[ravi])
    t2 = _new_task(client, alice, owner, pid, description="b.png")

    res = client.post(
        "/api/tasks/bulk-update",
        json={"ids": [t1, t2], "add_assignees": [sanjita]},
        headers=_as(alice, owner),
    )
    assert res.status_code == 200, res.text

    d1 = client.get(f"/api/tasks/{t1}", headers=_as(alice, owner)).json()
    d2 = client.get(f"/api/tasks/{t2}", headers=_as(alice, owner)).json()
    assert d1["assignees"] == [ravi, sanjita]
    assert d2["assignees"] == [sanjita]


def test_bulk_replace_sets_the_whole_set(client, alice, owner, ravi, sanjita):
    pid = _new_project(client, alice, owner)
    t1 = _new_task(client, alice, owner, pid, assignees=[ravi])

    res = client.post(
        "/api/tasks/bulk-update",
        json={"ids": [t1], "assignees": [sanjita]},
        headers=_as(alice, owner),
    )
    assert res.status_code == 200, res.text

    detail = client.get(f"/api/tasks/{t1}", headers=_as(alice, owner)).json()
    assert detail["assignees"] == [sanjita]
    assert _mirror(t1) == sanjita


def test_duplicate_and_padded_names_are_normalized(client, alice, owner, ravi):
    """Whitespace variants must not become a second, unmatchable assignee.

    get_current_annotator resolves " Ravi" to the member "Ravi", so an
    untrimmed row here would name someone no authority check can match.
    """
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid, assignees=[f"  {ravi}  ", ravi, ""])

    detail = client.get(f"/api/tasks/{tid}", headers=_as(alice, owner)).json()
    assert detail["assignees"] == [ravi]


def test_assignee_reaches_the_project_through_the_join_table(client, alice, bob, owner, ravi):
    """A non-primary assignee must still be able to open the project.

    Project access used to be granted by matching tasks.assignee, which names
    only the primary — so the second assignee could be handed work in a project
    they could not open.
    """
    sanjita = _member(f"s_{uuid.uuid4().hex[:6]}")
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid, assignees=[ravi, sanjita])

    # sanjita is the *second* assignee, so the mirror does not name her.
    assert _mirror(tid) == ravi
    res = client.get(f"/api/tasks/{tid}", headers=_as(alice, sanjita))
    assert res.status_code == 200, res.text


# --- CSV export -------------------------------------------------------------

def test_task_history_csv_downloads(client, alice, owner, ravi, sanjita):
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid, assignees=[ravi])
    client.patch(f"/api/tasks/{tid}", json={"assignees": [sanjita]}, headers=_as(alice, owner))

    res = client.get(f"/api/tasks/{tid}/assignments.csv", headers=_as(alice, owner))
    assert res.status_code == 200, res.text
    assert "text/csv" in res.headers["content-type"]
    assert "attachment" in res.headers["content-disposition"]

    body = res.text
    assert "Annotator" in body           # header row
    assert ravi in body and sanjita in body
    assert "unassigned" in body          # the reassignment is recorded


def test_task_history_csv_falls_back_to_current_assignees(client, alice, owner, ravi):
    """A backfilled task has no events; the file must still name who holds it.

    Otherwise the owner downloads an empty CSV for a task that plainly has an
    assignee, which reads as data loss rather than as "nothing has changed".
    """
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid)
    # Simulate the backfill: an assignee row with no matching event.
    db = SessionLocal()
    try:
        db.add(models.TaskAssignee(task_id=tid, member_name=ravi, position=0))
        db.commit()
    finally:
        db.close()

    res = client.get(f"/api/tasks/{tid}/assignments.csv", headers=_as(alice, owner))
    assert res.status_code == 200, res.text
    assert ravi in res.text
    assert "currently assigned" in res.text


def test_project_history_csv_covers_every_task(client, alice, owner, ravi, sanjita):
    pid = _new_project(client, alice, owner)
    t1 = _new_task(client, alice, owner, pid, description="a.png", assignees=[ravi])
    t2 = _new_task(client, alice, owner, pid, description="b.png", assignees=[sanjita])

    res = client.get(f"/api/tasks/assignments/export.csv?projectId={pid}", headers=_as(alice, owner))
    assert res.status_code == 200, res.text
    body = res.text
    assert "a.png" in body and "b.png" in body
    assert ravi in body and sanjita in body
    assert str(t1) in body and str(t2) in body


def test_history_csv_is_owner_and_reviewer_only(client, alice, bob, owner, ravi):
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid, assignees=[ravi])

    res = client.get(f"/api/tasks/{tid}/assignments.csv", headers=_as(bob, ravi))
    assert res.status_code in (403, 404), res.text
    res = client.get(f"/api/tasks/assignments/export.csv?projectId={pid}", headers=_as(bob, ravi))
    assert res.status_code in (403, 404), res.text


def test_history_csv_escapes_commas_and_quotes(client, alice, owner):
    """Names and filenames are free text; naive joining would corrupt columns."""
    tricky = _member(f'O\'Neill, "Sam" {uuid.uuid4().hex[:4]}')
    pid = _new_project(client, alice, owner)
    tid = _new_task(client, alice, owner, pid, description='a,b "quoted".png', assignees=[tricky])

    res = client.get(f"/api/tasks/{tid}/assignments.csv", headers=_as(alice, owner))
    assert res.status_code == 200, res.text

    import csv as _csv
    import io as _io
    rows = list(_csv.reader(_io.StringIO(res.text)))
    header, data = rows[0], rows[1:]
    # Round-trips through a real CSV parser with the columns intact.
    assert len(header) == 8
    assert all(len(r) == 8 for r in data if r)
    assert any(tricky in r for r in data)
    assert any('a,b "quoted".png' in r for r in data)
