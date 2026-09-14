"""Creating a class twice must yield one class.

On 2026-09-08 project 410 collected six classes named "object" in 37 minutes,
created by nobody: the canvas mints a fresh uuid for any class it has not seen
(`ensureLabel`), and POST /api/labels resolved by *id only*, so a fresh uuid
never matched an existing row and the endpoint always inserted. The frontend
half is fixed separately; this suite pins the server half, which is what makes
the guarantee hold for a stale bundle, a direct API caller, or any client yet
to be written.

See .devnotes/fix-class-creation/01_AUDIT.md and 02_PLAN.md phase 1.
"""
import itertools

_id_seq = itertools.count()


def _lid():
    return f"dedupe-{next(_id_seq)}"


def _new_project(client, auth, name="dedupe"):
    res = client.post(
        "/api/projects",
        json={"name": name, "slug": name, "creator": "ignored"},
        headers=auth,
    )
    return res.json()["id"]


def _labels(client, auth, pid):
    return client.get(f"/api/labels?projectId={pid}", headers=auth).json()


def _post(client, auth, pid, id_, name, color="#111"):
    return client.post(
        "/api/labels",
        json={"id": id_, "name": name, "color": color, "projectId": pid},
        headers=auth,
    )


# --- the incident ----------------------------------------------------------

def test_same_name_under_a_new_uuid_does_not_create_a_second_class(client, alice):
    """The exact 2026-09-08 shape, six times over."""
    pid = _new_project(client, alice)

    first = _post(client, alice, pid, _lid(), "object")
    assert first.status_code == 200, first.text
    assert first.json()["created"] is True
    canonical = first.json()["id"]

    # Five more opens of the same task, each minting a fresh uuid.
    for _ in range(5):
        res = _post(client, alice, pid, _lid(), "object")
        assert res.status_code == 200, res.text
        assert res.json()["created"] is False
        # The real id comes back, so the caller can repoint its annotation at
        # the class that exists rather than one only it knows about.
        assert res.json()["id"] == canonical

    names = [l["name"] for l in _labels(client, alice, pid)]
    assert names == ["object"], f"expected one class, got {names}"


def test_casing_and_underscores_collapse_to_one_class(client, alice):
    """D6: normalisation was client-side only, so the server stored variants."""
    pid = _new_project(client, alice)

    ids = set()
    for variant in ["Rust Area", "rust area", "RUST_AREA", "  Rust_Area  ", "rust  area"]:
        res = _post(client, alice, pid, _lid(), variant)
        assert res.status_code == 200, res.text
        ids.add(res.json()["id"])

    assert len(ids) == 1, "variants of one name created separate classes"
    labels = _labels(client, alice, pid)
    assert len(labels) == 1
    # The *display* name keeps the casing of whoever created the class first —
    # it is written into COCO `categories[].name` and the FastLabel `title` and
    # round-trips through import, so lowercasing it would be a real loss. Only
    # the matching key is folded.
    assert labels[0]["name"] == "Rust Area"


def test_bulk_upsert_also_resolves_by_name(client, alice):
    """S2 had the identical id-only lookup."""
    pid = _new_project(client, alice)
    _post(client, alice, pid, _lid(), "car")

    res = client.post(
        "/api/labels/bulk",
        json={
            "projectId": pid,
            "labels": [
                {"id": _lid(), "name": "Car", "color": "#abc", "projectId": pid},
                {"id": _lid(), "name": "truck", "color": "#def", "projectId": pid},
            ],
        },
        headers=alice,
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ok", "created": 1, "updated": 1}

    # "Car" matched the existing "car" by key and updated it; the bulk path
    # writes the display name it was given.
    names = sorted(l["name"] for l in _labels(client, alice, pid))
    assert names == ["Car", "truck"]


def test_bulk_upsert_collapses_duplicates_within_one_payload(client, alice):
    """Two payload entries normalising to one name must not collide."""
    pid = _new_project(client, alice)
    res = client.post(
        "/api/labels/bulk",
        json={
            "projectId": pid,
            "labels": [
                {"id": _lid(), "name": "Crack", "color": "#111", "projectId": pid},
                {"id": _lid(), "name": "crack", "color": "#222", "projectId": pid},
            ],
        },
        headers=alice,
    )
    assert res.status_code == 200, res.text
    assert len(_labels(client, alice, pid)) == 1


# --- what must keep working -------------------------------------------------

def test_posting_a_known_id_still_renames(client, alice):
    """The Classes edit modal is the only rename path; it must survive.

    This is why id resolution comes first: reading a rename as a create would
    silently leave the original row untouched.
    """
    pid = _new_project(client, alice)
    lid = _lid()
    _post(client, alice, pid, lid, "kar")

    res = _post(client, alice, pid, lid, "car", color="#999")
    assert res.status_code == 200, res.text
    assert res.json()["id"] == lid
    assert res.json()["created"] is False

    labels = _labels(client, alice, pid)
    assert len(labels) == 1
    assert labels[0]["name"] == "car"
    assert labels[0]["color"] == "#999"


def test_renaming_onto_an_existing_name_is_a_conflict(client, alice):
    """Merging two classes silently would move annotations with no way back."""
    pid = _new_project(client, alice)
    keep = _lid()
    _post(client, alice, pid, keep, "car")
    other = _lid()
    _post(client, alice, pid, other, "truck")

    res = _post(client, alice, pid, other, "Car")
    assert res.status_code == 409, res.text
    assert "car" in res.json()["detail"].lower()

    # Nothing moved.
    names = sorted(l["name"] for l in _labels(client, alice, pid))
    assert names == ["car", "truck"]


def test_the_same_name_in_a_different_project_is_a_different_class(client, alice):
    """Labels are per project; the constraint must be scoped, not global."""
    pid_a = _new_project(client, alice, "dedupe-a")
    pid_b = _new_project(client, alice, "dedupe-b")

    a = _post(client, alice, pid_a, _lid(), "object")
    b = _post(client, alice, pid_b, _lid(), "object")

    assert a.json()["id"] != b.json()["id"]
    assert a.json()["created"] is True
    assert b.json()["created"] is True
    assert len(_labels(client, alice, pid_a)) == 1
    assert len(_labels(client, alice, pid_b)) == 1


def test_recolouring_an_existing_class_still_works(client, alice):
    """Posting a known name with a new colour updates it, without duplicating."""
    pid = _new_project(client, alice)
    first = _post(client, alice, pid, _lid(), "car", color="#111")
    res = _post(client, alice, pid, _lid(), "car", color="#222")

    assert res.json()["id"] == first.json()["id"]
    labels = _labels(client, alice, pid)
    assert len(labels) == 1
    assert labels[0]["color"] == "#222"


def test_a_whitespace_only_name_cannot_create_a_nameless_class(client, alice):
    pid = _new_project(client, alice)
    res = _post(client, alice, pid, _lid(), "   ")
    assert res.status_code == 200, res.text
    labels = _labels(client, alice, pid)
    assert len(labels) == 1
    assert labels[0]["name"] == "object"


# --- the database-level backstop --------------------------------------------

def test_the_unique_index_exists_and_refuses_a_raw_duplicate():
    """The guarantee must not depend on the endpoint being the fixed one.

    A rule enforced only in today's code path is not a rule: a stale bundle, a
    direct API caller or a future endpoint would all bypass it. This asserts the
    constraint is really in the schema and really fires.
    """
    import pytest
    from sqlalchemy.exc import IntegrityError

    import models
    from database import SessionLocal
    from tests.conftest import unique_label_id

    db = SessionLocal()
    try:
        project = models.Project(name="idx", slug="idx", type="detection", status="New")
        db.add(project)
        db.flush()

        db.add(models.Label(id=unique_label_id(), name="object",
                            name_key="object", color="#111", project_id=project.id))
        db.flush()

        # Same key, different display casing: the index must still refuse it.
        db.add(models.Label(id=unique_label_id(), name="Object",
                            name_key="object", color="#222", project_id=project.id))
        with pytest.raises(IntegrityError):
            db.flush()
    finally:
        db.rollback()
        db.close()


def test_bulk_rename_onto_another_existing_class_is_a_conflict(client, alice):
    """The bulk path needs the same guard as the single one."""
    pid = _new_project(client, alice)
    keep, other = _lid(), _lid()
    _post(client, alice, pid, keep, "car")
    _post(client, alice, pid, other, "truck")

    res = client.post(
        "/api/labels/bulk",
        json={
            "projectId": pid,
            "labels": [{"id": other, "name": "car", "color": "#111", "projectId": pid}],
        },
        headers=alice,
    )
    assert res.status_code == 409, res.text
    names = sorted(l["name"] for l in _labels(client, alice, pid))
    assert names == ["car", "truck"], "the conflicting rename was applied anyway"


def test_bulk_renaming_a_class_to_its_own_name_is_not_a_conflict(client, alice):
    """A no-op rename must not trip the collision guard against itself."""
    pid = _new_project(client, alice)
    lid = _lid()
    _post(client, alice, pid, lid, "car")

    res = client.post(
        "/api/labels/bulk",
        json={
            "projectId": pid,
            "labels": [{"id": lid, "name": "Car", "color": "#999", "projectId": pid}],
        },
        headers=alice,
    )
    assert res.status_code == 200, res.text
    labels = _labels(client, alice, pid)
    assert len(labels) == 1
    assert labels[0]["color"] == "#999"
