"""The wipe guard: a save may not remove shapes the client did not delete.

Task 660 (2026-09-24) lost 975 of 976 annotations to a save carrying one
shape: its canvas had emptied itself after hydration, the annotator drew one
box, and the old guard — which only refused a completely *empty* payload —
accepted it. A ratio alone cannot stop that without also refusing the owner's
real bulk cleanup (31-57% losses, .devnotes/wipe-guard-bypass-fix/
05_IMPLEMENTATION.md §3), so the client names what it deleted in
`deleted_ids` and the server judges only the *unexplained* remainder.

Refused when the unexplained loss is >= WIPE_GUARD_MIN_LOST (10) and more than
WIPE_GUARD_RATIO (30%) of the stored set. An empty payload keeps its stricter
rule: any unexplained loss at all is refused.
See .devnotes/bulk-loss-guard/01_DESIGN.md.
"""
import json

import pytest
from sqlalchemy import event

from api.routers import tasks as tasks_router
from database import engine


def _project(client, auth):
    res = client.post("/api/projects", json={
        "name": "wipe-guard", "slug": "wipe-guard", "creator": "alice",
    }, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _create_task(client, auth, project_id):
    res = client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": "P1000920.JPG", "status": "New"},
        headers=auth,
    )
    assert res.status_code == 200, res.text
    return res.json()


def _shapes(ids):
    return [{"id": i, "type": "box", "labelId": "l1"} for i in ids]


def _ids(n, prefix="a"):
    return [f"{prefix}{i}" for i in range(n)]


def _save(client, auth, task_id, shapes, deleted_ids=None, client_id="tab-A", **extra):
    body = {
        "id": task_id,
        "annotations": json.dumps(shapes) if not isinstance(shapes, str) else shapes,
        "updated_at": None,
        "client_id": client_id,
        **extra,
    }
    if deleted_ids is not None:
        body["deleted_ids"] = deleted_ids
    return client.post("/api/tasks", json=body, headers=auth)


def _seed(client, auth, n):
    """A task holding `n` shapes a0..a{n-1}, last written by tab-A."""
    project_id = _project(client, auth)
    task = _create_task(client, auth, project_id)
    res = client.post("/api/tasks", json={
        "id": task["id"], "annotations": json.dumps(_shapes(_ids(n))),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=auth)
    assert res.status_code == 200, res.text
    return task["id"]


def _stored_ids(client, auth, task_id):
    detail = client.get(f"/api/tasks/{task_id}", headers=auth).json()
    return {a["id"] for a in detail["annotations"]}


@pytest.fixture
def events(monkeypatch):
    captured = []
    real = tasks_router.log_event

    def spy(name, **fields):
        captured.append((name, fields))
        return real(name, **fields)

    monkeypatch.setattr(tasks_router, "log_event", spy)
    return captured


def _named(events, name):
    return [f for (e, f) in events if e == name]


# ---------------------------------------------------------------------------
# The incidents, replayed at their real sizes
# ---------------------------------------------------------------------------

def test_task_660_replay_is_refused_and_nothing_is_lost(client, alice, events):
    """976 stored; the faulted tab sends only the one box drawn afterwards."""
    tid = _seed(client, alice, 976)
    events.clear()

    res = _save(client, alice, tid, _shapes(["new-box"]))

    assert res.status_code == 422, res.text
    assert "976" in res.json()["detail"]
    assert _stored_ids(client, alice, tid) == set(_ids(976)), "the refused save must write nothing"
    refused = _named(events, "task.save.refused_loss")
    assert len(refused) == 1
    assert refused[0]["removed"] == 976
    assert refused[0]["unexplained"] == 976
    assert refused[0]["reason"] == "unexplained_loss"
    assert _named(events, "task.save") == []


def test_task_660_as_a_real_delete_goes_through(client, alice):
    """The same payload is legitimate when the user deleted those 976 shapes."""
    tid = _seed(client, alice, 976)

    res = _save(client, alice, tid, _shapes(["new-box"]), deleted_ids=_ids(976))

    assert res.status_code == 200, res.text
    assert _stored_ids(client, alice, tid) == {"new-box"}


def test_task_1652_clear_all_goes_through(client, alice, events):
    """Clear all on 164 shapes, confirmed in the dialog, lists every id."""
    tid = _seed(client, alice, 164)
    events.clear()

    res = _save(client, alice, tid, "[]", deleted_ids=_ids(164))

    assert res.status_code == 200, res.text
    assert _stored_ids(client, alice, tid) == set()
    assert len(_named(events, "task.save.explicit_clear")) == 1


@pytest.mark.parametrize("before,after", [
    (221, 97),     # task 1131
    (548, 237),    # task 1189
    (153, 69),     # task 1588
    (1117, 773),   # task 1227
])
def test_the_owner_cleanups_that_sank_the_ratio_guard_now_pass(client, alice, before, after):
    """The four legitimate saves S1 would have refused, with their deletions named."""
    tid = _seed(client, alice, before)
    kept = _ids(before)[:after]
    deleted = _ids(before)[after:]

    res = _save(client, alice, tid, _shapes(kept), deleted_ids=deleted)

    assert res.status_code == 200, res.text
    assert _stored_ids(client, alice, tid) == set(kept)


def test_the_691_partial_wipe_is_refused(client, alice):
    """2631 -> 1194 (55%) with nothing deleted: the wipe the ratio guard targeted."""
    tid = _seed(client, alice, 2631)

    res = _save(client, alice, tid, _shapes(_ids(2631)[:1194]))

    assert res.status_code == 422, res.text
    assert len(_stored_ids(client, alice, tid)) == 2631


# ---------------------------------------------------------------------------
# Only the unexplained remainder counts
# ---------------------------------------------------------------------------

def test_a_confirmed_delete_cannot_carry_a_silent_loss_with_it(client, alice, events):
    """The flaw of a yes/no flag: the user deletes 3, the canvas had lost 87."""
    tid = _seed(client, alice, 100)
    events.clear()

    res = _save(client, alice, tid, _shapes(_ids(10)), deleted_ids=_ids(100)[10:13])

    assert res.status_code == 422, res.text
    refused = _named(events, "task.save.refused_loss")[0]
    assert refused["removed"] == 90
    assert refused["unexplained"] == 87
    assert len(_stored_ids(client, alice, tid)) == 100


def test_ids_the_server_does_not_hold_explain_nothing(client, alice):
    """Padding `deleted_ids` with unknown ids must not excuse a real loss."""
    tid = _seed(client, alice, 100)

    res = _save(client, alice, tid, _shapes(["x"]), deleted_ids=[f"zz{i}" for i in range(500)])

    assert res.status_code == 422, res.text


def test_deleted_ids_for_shapes_still_in_the_payload_are_harmless(client, alice):
    """An undone delete leaves its id in the list while the shape is back."""
    tid = _seed(client, alice, 50)

    res = _save(client, alice, tid, _shapes(_ids(50)), deleted_ids=_ids(50)[:5])

    assert res.status_code == 200, res.text
    assert len(_stored_ids(client, alice, tid)) == 50


def test_numeric_ids_match_their_stored_string_form(client, alice, events):
    """Rows store `str(id)`; a payload sending 7 for "7" is not a removal."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)
    numeric = [{"id": i, "type": "box"} for i in range(1, 41)]
    assert _save(client, alice, task["id"], numeric, updated_at=task["updated_at"]).status_code == 200

    res = _save(client, alice, task["id"], numeric[:20], deleted_ids=[str(i) for i in range(21, 41)])
    assert res.status_code == 200, res.text

    # Numeric deleted_ids are accepted and compared as strings: 6 and 7 explain
    # two of the 15 removed, leaving 13 unexplained of 20 stored (65%).
    events.clear()
    res = _save(client, alice, task["id"], numeric[:5], deleted_ids=[6, 7])
    assert res.status_code == 422, res.text
    refused = _named(events, "task.save.refused_loss")
    assert refused and refused[0]["removed"] == 15 and refused[0]["unexplained"] == 13

    res = _save(client, alice, task["id"], numeric[:5], deleted_ids=list(range(6, 21)))
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# Thresholds: small or proportionally minor losses never block
# ---------------------------------------------------------------------------

def test_below_the_absolute_floor_is_allowed(client, alice):
    """8 -> 2 with nothing listed: 6 unexplained, under the floor of 10."""
    tid = _seed(client, alice, 8)

    res = _save(client, alice, tid, _shapes(_ids(2)))

    assert res.status_code == 200, res.text


def test_under_the_ratio_is_allowed(client, alice):
    """100 -> 75: 25 unexplained is 25%, under 30%."""
    tid = _seed(client, alice, 100)

    assert _save(client, alice, tid, _shapes(_ids(75))).status_code == 200


def test_exactly_at_the_ratio_is_allowed(client, alice):
    """100 -> 70: 30 unexplained is exactly 30%, and the test is strictly `>`."""
    tid = _seed(client, alice, 100)

    assert _save(client, alice, tid, _shapes(_ids(70))).status_code == 200


def test_just_over_the_ratio_is_refused(client, alice):
    """100 -> 69: 31 unexplained, 31%."""
    tid = _seed(client, alice, 100)

    assert _save(client, alice, tid, _shapes(_ids(69))).status_code == 422


def test_thresholds_come_from_config(client, alice, monkeypatch):
    """Rule 12: the values are read from config at request time, not copied."""
    monkeypatch.setattr(tasks_router._cfg, "WIPE_GUARD_MIN_LOST", 100)
    tid = _seed(client, alice, 100)

    assert _save(client, alice, tid, _shapes(_ids(20))).status_code == 200


# ---------------------------------------------------------------------------
# The empty payload keeps its stricter rule
# ---------------------------------------------------------------------------

def test_an_empty_payload_with_nothing_listed_is_refused(client, alice, events):
    tid = _seed(client, alice, 3)
    events.clear()

    res = _save(client, alice, tid, "[]")

    assert res.status_code == 422, res.text
    assert _named(events, "task.save.refused_clear")[0]["reason"] == "allow_clear_missing"
    assert len(_stored_ids(client, alice, tid)) == 3


def test_an_empty_payload_needs_every_id_listed(client, alice):
    """Below both thresholds, but emptying a task is never a partial accident."""
    tid = _seed(client, alice, 3)

    res = _save(client, alice, tid, "[]", deleted_ids=_ids(3)[:2])

    assert res.status_code == 422, res.text


def test_legacy_allow_clear_still_clears(client, alice):
    """Bundles cached from before `deleted_ids` send only `allow_clear`."""
    tid = _seed(client, alice, 40)

    res = _save(client, alice, tid, "[]", allow_clear=True)

    assert res.status_code == 200, res.text
    assert _stored_ids(client, alice, tid) == set()


def test_allow_clear_does_not_authorise_a_partial_loss(client, alice):
    """The legacy flag covers emptying only; it is no blank cheque."""
    tid = _seed(client, alice, 100)

    res = _save(client, alice, tid, _shapes(["x"]), allow_clear=True)

    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# Everything else is untouched
# ---------------------------------------------------------------------------

def test_growth_and_edits_are_unaffected(client, alice):
    tid = _seed(client, alice, 50)
    edited = _shapes(_ids(50)) + _shapes(_ids(30, prefix="n"))
    edited[0]["labelId"] = "l2"

    assert _save(client, alice, tid, edited).status_code == 200
    assert len(_stored_ids(client, alice, tid)) == 80


def test_a_time_only_save_is_unaffected(client, alice):
    tid = _seed(client, alice, 50)

    res = client.post("/api/tasks", json={
        "id": tid, "time_spent_delta": 30, "client_id": "tab-A",
    }, headers=alice)
    assert res.status_code == 200, res.text


def test_a_task_with_no_stored_shapes_is_never_refused(client, alice):
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    assert _save(client, alice, task["id"], _shapes(_ids(5)), updated_at=task["updated_at"]).status_code == 200


def test_a_refused_save_changes_nothing_else_either(client, alice):
    """Status and time ride the same request; a refusal must not half-apply it."""
    tid = _seed(client, alice, 100)
    before = client.get(f"/api/tasks/{tid}", headers=alice).json()

    res = _save(client, alice, tid, _shapes(["x"]), status="Completed", time_spent_delta=60)
    assert res.status_code == 422

    after = client.get(f"/api/tasks/{tid}", headers=alice).json()
    assert after["status"] == before["status"]
    assert after["time_spent"] == before["time_spent"]
    assert after["updated_at"] == before["updated_at"]


def test_the_refusal_is_not_a_conflict(client, alice):
    """422, never 409: the client's 409 'keep mine' would resend it forever."""
    tid = _seed(client, alice, 100)

    assert _save(client, alice, tid, _shapes(["x"])).status_code == 422


# ---------------------------------------------------------------------------
# Cost: the guard reuses what the save already loads
# ---------------------------------------------------------------------------

def test_the_guard_adds_no_query_for_the_stored_rows(client, alice):
    """Stored ids come from the collection the row sync loads anyway.

    Exactly one SELECT of the task's annotation rows per save — the guard
    reading them first must not make the sync read them a second time.
    """
    tid = _seed(client, alice, 200)
    statements = []

    def record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        res = _save(client, alice, tid, _shapes(_ids(150)), deleted_ids=_ids(200)[150:])
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert res.status_code == 200, res.text
    row_loads = [
        s for s in statements
        if s.lstrip().upper().startswith("SELECT")
        and "FROM annotations" in s
        and "count(" not in s.lower()
    ]
    assert len(row_loads) == 1, row_loads
