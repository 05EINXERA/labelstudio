"""Hiding a project: the owner takes it out of everyone else's view.

Hidden is owner-only visibility, enforced where access is decided
(`get_owned_project`, the projects list, and `_accessible_project_ids` for
tasks), and it leaves assignments and appointments in place so unhiding
restores access exactly as it was.

The shared-login case is the one that matters: `is_project_creator` is true
for every annotator profile on the owning account, so the hide is keyed on
`sees_hidden_project` instead — otherwise it would hide the project from nobody
in the deployment this app actually runs in.
"""


def _new_project(client, auth, name="hid-proj"):
    res = client.post(
        "/api/projects", json={"name": name, "slug": name, "creator": "ignored"}, headers=auth
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _new_task(client, auth, pid, assignee=None):
    res = client.post(
        "/api/tasks", params={"projectId": pid}, json={"description": "a.png"}, headers=auth
    )
    assert res.status_code == 200, res.text
    tid = res.json()["id"]
    if assignee:
        res = client.patch(f"/api/tasks/{tid}?projectId={pid}", json={"assignee": assignee}, headers=auth)
        assert res.status_code == 200, res.text
    return tid


def _member(client, auth, name):
    """Ensure a TeamMember row exists; get_current_annotator returns None without one."""
    client.post("/api/team/ping", headers={**auth, "X-Annotator-Name": name})
    return name


def _set_hidden(client, auth, pid, hidden):
    return client.patch(f"/api/projects/{pid}", json={"hidden": hidden}, headers=auth)


def _listed_ids(client, auth):
    return [p["id"] for p in client.get("/api/projects", headers=auth).json()]


def test_projects_start_visible(client, alice):
    pid = _new_project(client, alice)
    assert client.get(f"/api/projects/{pid}", headers=alice).json()["hidden"] is False
    assert client.get("/api/projects", headers=alice).json()[0]["hidden"] is False


def test_owner_still_sees_a_hidden_project(client, alice):
    pid = _new_project(client, alice)
    tid = _new_task(client, alice, pid)

    assert _set_hidden(client, alice, pid, True).status_code == 200

    listed = client.get("/api/projects", headers=alice).json()
    assert [(p["id"], p["hidden"]) for p in listed] == [(pid, True)]
    assert client.get(f"/api/projects/{pid}", headers=alice).json()["hidden"] is True
    assert client.get(f"/api/tasks/{tid}", headers=alice).status_code == 200


def test_hidden_project_disappears_for_an_assignee_and_returns_on_unhide(client, alice, bob):
    pid = _new_project(client, alice)
    annot = _member(client, alice, "Annot")
    tid = _new_task(client, alice, pid, assignee=annot)
    bob_annot = {**bob, "X-Annotator-Name": annot}

    assert _listed_ids(client, bob_annot) == [pid]
    assert client.get(f"/api/tasks/{tid}", headers=bob_annot).status_code == 200

    _set_hidden(client, alice, pid, True)

    assert _listed_ids(client, bob_annot) == []
    assert client.get(f"/api/projects/{pid}", headers=bob_annot).status_code == 404
    assert client.get(f"/api/tasks?projectId={pid}", headers=bob_annot).status_code == 404
    assert client.get(f"/api/tasks/{tid}", headers=bob_annot).status_code == 404
    # The cross-project task search must not surface it either.
    assert client.get("/api/tasks", headers=bob_annot).json()["items"] == []

    _set_hidden(client, alice, pid, False)

    # The assignment was never touched, so access is back without re-assigning.
    assert _listed_ids(client, bob_annot) == [pid]
    assert client.get(f"/api/tasks/{tid}", headers=bob_annot).status_code == 200


def test_hidden_project_is_hidden_from_reviewers_too(client, alice, bob):
    pid = _new_project(client, alice)
    rev = _member(client, alice, "Rev")
    client.post(f"/api/projects/{pid}/reviewers", json={"member_name": rev}, headers=alice)
    bob_rev = {**bob, "X-Annotator-Name": rev}
    assert client.get(f"/api/projects/{pid}", headers=bob_rev).status_code == 200

    _set_hidden(client, alice, pid, True)

    assert client.get(f"/api/projects/{pid}", headers=bob_rev).status_code == 404
    assert _listed_ids(client, bob_rev) == []


def test_hidden_from_other_profiles_on_the_shared_login(client, alice):
    """One account, several annotator profiles: only the creator's keeps it."""
    owner_name = _member(client, alice, "Owner")
    other_name = _member(client, alice, "Other")
    as_owner = {**alice, "X-Annotator-Name": owner_name}
    as_other = {**alice, "X-Annotator-Name": other_name}

    pid = _new_project(client, as_owner)
    tid = _new_task(client, as_owner, pid, assignee=other_name)
    assert _listed_ids(client, as_other) == [pid]

    assert _set_hidden(client, as_owner, pid, True).status_code == 200

    assert _listed_ids(client, as_owner) == [pid]
    assert _listed_ids(client, as_other) == []
    assert client.get(f"/api/projects/{pid}", headers=as_other).status_code == 404
    assert client.get(f"/api/tasks/{tid}", headers=as_other).status_code == 404
    # Nor can another profile bring it back.
    assert _set_hidden(client, as_other, pid, False).status_code == 404
    assert client.get(f"/api/projects/{pid}", headers=as_owner).json()["hidden"] is True


def test_another_profile_cannot_hide_a_visible_project(client, alice):
    """It would vanish for the one who hid it, with no way for them to undo it."""
    owner_name = _member(client, alice, "Owner")
    other_name = _member(client, alice, "Other")
    as_owner = {**alice, "X-Annotator-Name": owner_name}
    as_other = {**alice, "X-Annotator-Name": other_name}
    pid = _new_project(client, as_owner)

    assert _set_hidden(client, as_other, pid, True).status_code == 403
    assert client.get(f"/api/projects/{pid}", headers=as_owner).json()["hidden"] is False


def test_editing_other_fields_leaves_visibility_alone(client, alice):
    pid = _new_project(client, alice)
    _set_hidden(client, alice, pid, True)

    res = client.patch(f"/api/projects/{pid}", json={"name": "renamed"}, headers=alice)
    assert res.status_code == 200

    assert client.get(f"/api/projects/{pid}", headers=alice).json()["hidden"] is True
