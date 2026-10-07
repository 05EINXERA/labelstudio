"""Tasks in a review-outcome status are out of the annotators' sight.

Approved / Verified / Checked / Passed / Monitored / Reviewed tasks stay
visible to the project owner and its appointed reviewers only. The rule covers
the read paths (list, workspace sequence, detail, cross-project search); the
save path is untouched so an open tab never loses work to it.

As in test_project_hidden.py, the shared-login case is the one that matters:
ownership here is the profile-strict rule, because `is_project_creator` is true
for every annotator profile on the owning account.
"""
import pytest

from api.routers.tasks import REVIEW_HIDDEN_STATUSES


def _new_project(client, auth, name="rev-vis-proj"):
    res = client.post(
        "/api/projects", json={"name": name, "slug": name, "creator": "ignored"}, headers=auth
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _new_task(client, auth, pid, description="a.png", assignee=None, status=None):
    res = client.post(
        "/api/tasks", params={"projectId": pid}, json={"description": description}, headers=auth
    )
    assert res.status_code == 200, res.text
    tid = res.json()["id"]
    patch = {k: v for k, v in (("assignee", assignee), ("status", status)) if v}
    if patch:
        res = client.patch(f"/api/tasks/{tid}?projectId={pid}", json=patch, headers=auth)
        assert res.status_code == 200, res.text
    return tid


def _member(client, auth, name):
    """Ensure a TeamMember row exists; get_current_annotator returns None without one."""
    client.post("/api/team/ping", headers={**auth, "X-Annotator-Name": name})
    return name


def _list_ids(client, auth, pid=None):
    params = {"projectId": pid} if pid else {}
    res = client.get("/api/tasks", params=params, headers=auth)
    assert res.status_code == 200, res.text
    return sorted(t["id"] for t in res.json()["items"])


def _sequence_ids(client, auth, pid):
    res = client.get(f"/api/tasks/sequence/{pid}", headers=auth)
    assert res.status_code == 200, res.text
    return sorted(t["id"] for t in res.json())


@pytest.mark.parametrize("status", sorted(REVIEW_HIDDEN_STATUSES))
def test_reviewed_task_is_hidden_from_its_assignee(client, alice, bob, status):
    pid = _new_project(client, alice)
    annot = _member(client, alice, "Annot")
    open_tid = _new_task(client, alice, pid, "open.png", assignee=annot)
    done_tid = _new_task(client, alice, pid, "done.png", assignee=annot, status=status)
    bob_annot = {**bob, "X-Annotator-Name": annot}

    assert _list_ids(client, bob_annot, pid) == [open_tid]
    assert _sequence_ids(client, bob_annot, pid) == [open_tid]
    assert _list_ids(client, bob_annot) == [open_tid]  # cross-project search
    assert client.get(f"/api/tasks/{done_tid}", headers=bob_annot).status_code == 404
    assert client.get(f"/api/tasks/{open_tid}", headers=bob_annot).status_code == 200


@pytest.mark.parametrize("status", ["New", "In Progress", "Completed", "Declined"])
def test_working_statuses_stay_visible(client, alice, bob, status):
    """Finished-but-unreviewed and sent-back work must stay with the annotator."""
    pid = _new_project(client, alice)
    annot = _member(client, alice, "Annot")
    tid = _new_task(client, alice, pid, assignee=annot, status=status)
    bob_annot = {**bob, "X-Annotator-Name": annot}

    assert _list_ids(client, bob_annot, pid) == [tid]
    assert client.get(f"/api/tasks/{tid}", headers=bob_annot).status_code == 200


def test_owner_and_reviewer_still_see_reviewed_tasks(client, alice, bob):
    pid = _new_project(client, alice)
    rev = _member(client, alice, "Rev")
    client.post(f"/api/projects/{pid}/reviewers", json={"member_name": rev}, headers=alice)
    tid = _new_task(client, alice, pid, status="Approved")
    bob_rev = {**bob, "X-Annotator-Name": rev}

    for auth in (alice, bob_rev):
        assert _list_ids(client, auth, pid) == [tid]
        assert _sequence_ids(client, auth, pid) == [tid]
        assert _list_ids(client, auth) == [tid]
        assert client.get(f"/api/tasks/{tid}", headers=auth).status_code == 200


def test_task_returns_when_sent_back(client, alice, bob):
    pid = _new_project(client, alice)
    annot = _member(client, alice, "Annot")
    tid = _new_task(client, alice, pid, assignee=annot, status="Approved")
    bob_annot = {**bob, "X-Annotator-Name": annot}
    assert _list_ids(client, bob_annot, pid) == []

    res = client.patch(f"/api/tasks/{tid}?projectId={pid}", json={"status": "Declined"}, headers=alice)
    assert res.status_code == 200, res.text

    assert _list_ids(client, bob_annot, pid) == [tid]
    assert client.get(f"/api/tasks/{tid}", headers=bob_annot).status_code == 200


def test_hidden_from_other_profiles_on_the_shared_login(client, alice):
    """One account, several profiles: the creator's profile sees it, an annotator's does not."""
    owner_name = _member(client, alice, "Owner")
    annot = _member(client, alice, "Annot")
    as_owner = {**alice, "X-Annotator-Name": owner_name}
    as_annot = {**alice, "X-Annotator-Name": annot}
    pid = _new_project(client, as_owner)
    tid = _new_task(client, as_owner, pid, assignee=annot, status="Verified")

    assert _list_ids(client, as_owner, pid) == [tid]
    assert _list_ids(client, as_annot, pid) == []
    assert _sequence_ids(client, as_annot, pid) == []
    assert client.get(f"/api/tasks/{tid}", headers=as_annot).status_code == 404


def test_save_to_a_reviewed_task_is_not_turned_into_a_404(client, alice, bob):
    """A tab that had the task open when it was approved must not lose its save."""
    pid = _new_project(client, alice)
    annot = _member(client, alice, "Annot")
    tid = _new_task(client, alice, pid, assignee=annot, status="Approved")
    bob_annot = {**bob, "X-Annotator-Name": annot}

    res = client.post(
        "/api/tasks", params={"projectId": pid},
        json={"id": tid, "annotations": "[]", "time_spent_delta": 1}, headers=bob_annot,
    )
    assert res.status_code == 200, res.text
