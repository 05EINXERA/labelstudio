"""End-to-end: real requests through the app produce the right service log lines.

The unit tests in test_logging_service.py cover the writer; these cover the
wiring — that the middleware is actually installed, that endpoints reach it
with their event detail, and that the motivating query (find the save that
reduced an annotator's object count) really works against what gets written.

The writer is redirected to a tmp directory per test rather than pointed at the
real LOG_DIR, so a run never appends to the developer's own logs.
"""
import json

import pytest

import logging_service
from logging_service import ServiceLogWriter


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    """Point the module-level writer at a temp directory for one test."""
    replacement = ServiceLogWriter(base_dir=str(tmp_path / "service"))
    monkeypatch.setattr(logging_service, "writer", replacement)
    return tmp_path / "service"


def read_lines(logdir, name):
    """Every line written to `<name>.log`, across whatever day(s) it landed in.

    Days are enumerated rather than assumed, so a run that straddles midnight
    does not fail spuriously.
    """
    lines = []
    if not logdir.exists():
        return lines
    for day in sorted(logdir.iterdir()):
        path = day / f"{name}.log"
        if path.exists():
            lines.extend(
                line for line in path.read_text(encoding="utf-8").splitlines() if line
            )
    return lines


def fields_of(line):
    """Parse the `k=v` tail of a log line into a dict."""
    out = {}
    for token in line.split():
        if "=" in token:
            key, _, value = token.partition("=")
            out[key] = value
    return out


def _project(client, auth, name="Log Project"):
    res = client.post("/api/projects", json={"name": name, "slug": name.lower().replace(" ", "-"), "type": "detection", "creator": "test"}, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _task(client, auth, project_id, annotations=None):
    payload = {"description": "img.jpg", "status": "New"}
    if annotations is not None:
        payload["annotations"] = json.dumps(annotations)
    res = client.post(f"/api/tasks?projectId={project_id}", json=payload, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _box(label="car"):
    return {"type": "rect", "label": label, "x": 1, "y": 2, "w": 3, "h": 4}


# --- the core requirement -------------------------------------------------

def test_a_save_logs_user_task_and_both_object_counts(client, alice, logdir):
    project = _project(client, alice)
    task = _task(client, alice, project, [_box(), _box(), _box()])

    detail = client.get(f"/api/tasks/{task}", headers=alice).json()
    res = client.post(
        "/api/tasks",
        json={
            "id": task,
            "annotations": json.dumps([_box()]),
            "updated_at": detail["updated_at"],
            "client_id": "tab-1",
            "object_count": 1,
        },
        headers=alice,
    )
    assert res.status_code == 200, res.text

    saves = [l for l in read_lines(logdir, "POST") if "event=task.save " in l + " "]
    assert saves, read_lines(logdir, "POST")
    f = fields_of(saves[-1])
    assert f["task"] == str(task)
    assert f["objects"] == "1"
    assert f["objects_prev"] == "3"
    assert f["objects_client"] == "1"
    assert f["delta"] == "-2"
    assert f["client"] == "tab-1"
    assert f["user"].startswith("alice")


def test_the_loss_query_finds_exactly_the_destructive_save(client, alice, logdir):
    """The motivating use case, run end to end.

    An annotator saves twice: once adding, once removing. Grepping for a
    negative delta must return the second save and not the first — that single
    query is what replaces running a database script when someone reports lost
    work. See .devnotes/logging/02_PLAN.md §5.
    """
    project = _project(client, alice)
    task = _task(client, alice, project, [_box()])

    for annotations in ([_box(), _box(), _box()], [_box()]):
        detail = client.get(f"/api/tasks/{task}", headers=alice).json()
        res = client.post(
            "/api/tasks",
            json={
                "id": task,
                "annotations": json.dumps(annotations),
                "updated_at": detail["updated_at"],
                "client_id": "tab-1",
                "object_count": len(annotations),
            },
            headers=alice,
        )
        assert res.status_code == 200, res.text

    regressions = [
        line for line in read_lines(logdir, "POST")
        if "event=task.save " in line + " " and " delta=-" in line
    ]
    assert len(regressions) == 1
    f = fields_of(regressions[0])
    assert (f["objects_prev"], f["objects"], f["delta"]) == ("3", "1", "-2")


def test_object_count_is_optional_and_a_mismatch_is_logged_not_rejected(client, alice, logdir):
    """A stale client omits it; a buggy one disagrees. Neither may fail a save.

    The count is diagnostic only (plan D4) — refusing a save over it would make
    the logging feature a cause of the data loss it exists to explain.
    """
    project = _project(client, alice)
    task = _task(client, alice, project, [_box()])

    detail = client.get(f"/api/tasks/{task}", headers=alice).json()
    res = client.post(
        "/api/tasks",
        json={"id": task, "annotations": json.dumps([_box(), _box()]),
              "updated_at": detail["updated_at"], "client_id": "tab-1",
              "object_count": 99},  # deliberately wrong
        headers=alice,
    )
    assert res.status_code == 200, res.text

    f = fields_of([l for l in read_lines(logdir, "POST") if "event=task.save " in l + " "][-1])
    assert f["objects"] == "2"          # the server's own count of the blob
    assert f["objects_client"] == "99"  # what the panel claimed
    # Both are on the line; the disagreement is the finding, not an error.

    detail = client.get(f"/api/tasks/{task}", headers=alice).json()
    res = client.post(
        "/api/tasks",
        json={"id": task, "annotations": json.dumps([_box()]),
              "updated_at": detail["updated_at"], "client_id": "tab-1"},
        headers=alice,
    )
    assert res.status_code == 200, res.text
    f = fields_of([l for l in read_lines(logdir, "POST") if "event=task.save " in l + " "][-1])
    assert f["objects_client"] == "-"


# --- deletes and other mutations -----------------------------------------

def test_delete_is_logged_at_warn_with_what_it_destroyed(client, alice, logdir):
    project = _project(client, alice)
    task = _task(client, alice, project, [_box(), _box()])

    assert client.delete(f"/api/tasks/{task}", headers=alice).status_code == 200

    deletes = [l for l in read_lines(logdir, "DELETE") if "event=task.delete" in l]
    assert len(deletes) == 1
    assert " WARN " in deletes[0]
    f = fields_of(deletes[0])
    assert f["task"] == str(task)
    assert f["objects"] == "2"


def test_bulk_delete_records_the_count_and_the_ids(client, alice, logdir):
    project = _project(client, alice)
    ids = [_task(client, alice, project, [_box()]) for _ in range(3)]

    res = client.post("/api/tasks/bulk-delete", json={"ids": ids}, headers=alice)
    assert res.status_code == 200, res.text

    line = [l for l in read_lines(logdir, "POST") if "event=task.bulk_delete" in l][0]
    assert " WARN " in line
    f = fields_of(line)
    assert f["deleted"] == "3"
    assert f["objects"] == "3"
    for task_id in ids:
        assert str(task_id) in f["ids"]


def test_every_destructive_action_is_findable_by_grepping_warn(client, alice, logdir):
    """The operator-facing promise from plan §3.

    Nobody should need to memorise event names to audit what was destroyed;
    filtering on WARN must be enough.
    """
    project = _project(client, alice)
    task = _task(client, alice, project, [_box()])
    client.delete(f"/api/tasks/{task}", headers=alice)
    client.delete(f"/api/projects/{project}", headers=alice)

    warns = [l for l in read_lines(logdir, "DELETE") if " WARN " in l]
    events = {fields_of(l)["event"] for l in warns}
    assert {"task.delete", "project.delete"} <= events


def test_a_refused_clear_is_logged_with_the_count_it_protected(client, alice, logdir):
    """The 422 clear-guard. This is the line that proves work was *not* lost."""
    project = _project(client, alice)
    task = _task(client, alice, project, [_box(), _box()])

    detail = client.get(f"/api/tasks/{task}", headers=alice).json()
    res = client.post(
        "/api/tasks",
        json={"id": task, "annotations": "[]", "updated_at": detail["updated_at"],
              "client_id": "tab-1", "object_count": 0},
        headers=alice,
    )
    assert res.status_code == 422

    line = [l for l in read_lines(logdir, "POST") if "event=task.save.refused_clear" in l][0]
    f = fields_of(line)
    assert f["objects_prev"] == "2"
    assert f["reason"] == "allow_clear_missing"
    # And it is duplicated into errors.log, since it was a non-2xx.
    assert any("refused_clear" in l for l in read_lines(logdir, "errors"))


def test_a_conflict_records_both_client_ids(client, alice, logdir):
    project = _project(client, alice)
    task = _task(client, alice, project, [_box()])

    detail = client.get(f"/api/tasks/{task}", headers=alice).json()
    client.post("/api/tasks", json={
        "id": task, "annotations": json.dumps([_box(), _box()]),
        "updated_at": detail["updated_at"], "client_id": "tab-1",
    }, headers=alice)

    # A second tab writing against the now-stale token it read earlier.
    res = client.post("/api/tasks", json={
        "id": task, "annotations": json.dumps([_box()]),
        "updated_at": detail["updated_at"], "client_id": "tab-2",
    }, headers=alice)
    assert res.status_code == 409

    line = [l for l in read_lines(logdir, "POST") if "event=task.save.conflict" in l][0]
    f = fields_of(line)
    assert f["client"] == "tab-2"
    assert f["last_client"] == "tab-1"


# --- routing, identity and noise control ----------------------------------

def test_a_failure_lands_in_errors_log_as_well_as_the_method_file(client, alice, bob, logdir):
    project = _project(client, alice)
    task = _task(client, alice, project, [_box()])

    # bob has no role on alice's project: 404 by design (ids must not be
    # enumerable), which is still a non-2xx and belongs in errors.log.
    res = client.get(f"/api/tasks/{task}", headers=bob)
    assert res.status_code in (403, 404)

    errors = read_lines(logdir, "errors")
    assert any(f"/api/tasks/{task}" in l for l in errors)


def test_static_assets_and_the_health_probe_are_not_logged(client, logdir):
    client.get("/health")
    assert read_lines(logdir, "GET") == []


def test_the_authenticated_username_is_on_the_line(client, alice, logdir):
    _project(client, alice)
    line = [l for l in read_lines(logdir, "POST") if "event=project.create" in l][0]
    assert fields_of(line)["user"].startswith("alice")


def test_a_login_names_the_account_even_though_it_predates_authentication(client, logdir):
    """`get_current_user` never runs on /token, so the handler must set the user.

    Without that, the single most useful auth line would read `user=-`.
    """
    res = client.post("/api/auth/register", json={"username": "loguser-x", "password": "pw-12345"})
    assert res.status_code == 200
    client.cookies.clear()
    res = client.post("/api/auth/token", data={"username": "loguser-x", "password": "pw-12345"})
    assert res.status_code == 200

    line = [l for l in read_lines(logdir, "POST") if "event=auth.login " in l + " "][0]
    assert fields_of(line)["user"] == "loguser-x"


def test_a_failed_login_is_warned_without_leaking_the_password(client, logdir):
    client.post("/api/auth/token", data={"username": "nobody-here", "password": "hunter2"})
    line = [l for l in read_lines(logdir, "POST") if "event=auth.login_failed" in l][0]
    assert " WARN " in line
    assert "hunter2" not in line


def test_the_response_carries_the_correlation_id(client, alice):
    res = client.get("/api/tasks", headers=alice)
    assert len(res.headers.get("X-Request-Id", "")) == 8
