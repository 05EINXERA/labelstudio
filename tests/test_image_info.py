"""Images Info — `GET /api/projects/{id}/image-info`.

Plan: `.devnotes/image-size-check/01_PLAN.md`.

The feature's whole value is being *right* about what is in a project, so these
tests lean on the cases where a naive implementation would be quietly wrong
rather than loudly broken: an unmeasured image folded into the wrong bucket, a
missing file reported as zero bytes, a summary that describes the page instead
of the filtered set.
"""
import os

import models
from config import DATA_DIR
from database import SessionLocal

FULL = (5184, 3888)
HALF = (2592, 1944)


def _project(client, headers, name="P"):
    return client.post(
        "/api/projects",
        json={"name": name, "slug": name.lower(), "creator": "ignored"},
        headers=headers,
    ).json()["id"]


def _task(project_id, filename, width=None, height=None, content=None, status="New"):
    """Create a task row directly, optionally writing a real file for it.

    Direct DB insert rather than the upload endpoint: these tests need to
    control the stored dimensions precisely, including the NULL and zero states
    that the upload path never produces but that real production rows carry.
    """
    image_path = None
    if content is not None:
        uploads = os.path.join(DATA_DIR, "uploads")
        os.makedirs(uploads, exist_ok=True)
        name = f"{project_id}-{filename}"
        with open(os.path.join(uploads, name), "wb") as fh:
            fh.write(content)
        image_path = f"uploads/{name}"

    db = SessionLocal()
    try:
        task = models.Task(
            project_id=project_id, description=filename, status=status,
            image_width=width, image_height=height, image_path=image_path,
        )
        db.add(task)
        db.commit()
        return task.id
    finally:
        db.close()


def _get(client, headers, project_id, **params):
    return client.get(
        f"/api/projects/{project_id}/image-info", params=params, headers=headers
    )


# --- permissions -------------------------------------------------------------


def test_requires_authentication(client):
    project_id = None
    res = client.get("/api/projects/1/image-info")
    assert res.status_code == 401
    assert project_id is None


def test_no_role_is_404_not_403(client, alice, bob):
    """The id-enumeration contract (CLAUDE.md rule 1b): a caller with no role
    must not be able to tell a project they cannot see from one that does not
    exist."""
    project_id = _project(client, alice)
    assert _get(client, bob, project_id).status_code == 404
    assert _get(client, bob, 999999).status_code == 404


def test_viewer_may_read_the_table(client, alice, bob):
    """Viewer, not reviewer: the Tasks view already shows this caller every
    filename, and the resolution is visible the moment they open the image."""
    project_id = _project(client, alice)
    team = client.post("/api/teams", json={"name": "T"}, headers=alice).json()
    username = client.get("/api/auth/me", headers=bob).json()["username"]
    client.post(
        f"/api/teams/{team['id']}/members",
        json={"username": username, "role": "member"}, headers=alice,
    )
    client.post(
        f"/api/projects/{project_id}/grants",
        json={"team_id": team["id"], "role": "viewer"}, headers=alice,
    )

    assert _get(client, bob, project_id).status_code == 200


# --- categorisation ----------------------------------------------------------


def test_categorises_the_two_known_sizes(client, alice):
    project_id = _project(client, alice)
    _task(project_id, "full.jpg", *FULL)
    _task(project_id, "half.jpg", *HALF)

    body = _get(client, alice, project_id).json()
    by_name = {r["filename"]: r for r in body["items"]}
    assert by_name["full.jpg"]["category"] == "Full"
    assert by_name["half.jpg"]["category"] == "Half"


def test_unmeasured_rows_are_unknown_not_other(client, alice):
    """NULL dimensions mean 'run the backfill', not 'an unusual image'. Folding
    them into Other would hide a data gap behind a plausible bucket — and this
    endpoint exists precisely to be trusted about what is in the project."""
    project_id = _project(client, alice)
    _task(project_id, "never-measured.jpg", None, None)
    # (0, 0) is what formats.common.image_size() writes for an unreadable file;
    # it is no more measured than NULL.
    _task(project_id, "unreadable.jpg", 0, 0)

    body = _get(client, alice, project_id).json()
    assert {r["category"] for r in body["items"]} == {"Unknown"}
    assert body["summary"]["by_category"]["Unknown"] == 2
    assert body["summary"]["by_category"]["Other"] == 0


def test_a_third_resolution_lands_in_other(client, alice):
    project_id = _project(client, alice)
    _task(project_id, "phone.jpg", 4032, 3024)
    # Transposed is a different image, not the same category.
    _task(project_id, "portrait.jpg", 3888, 5184)

    body = _get(client, alice, project_id).json()
    assert {r["category"] for r in body["items"]} == {"Other"}
    assert body["summary"]["by_category"]["Other"] == 2


# --- filtering ---------------------------------------------------------------


def test_category_filter_selects_only_that_category(client, alice):
    project_id = _project(client, alice)
    _task(project_id, "a-full.jpg", *FULL)
    _task(project_id, "b-full.jpg", *FULL)
    _task(project_id, "c-half.jpg", *HALF)

    body = _get(client, alice, project_id, category="Half").json()
    assert [r["filename"] for r in body["items"]] == ["c-half.jpg"]
    assert body["total"] == 1


def test_other_filter_excludes_unmeasured_rows(client, alice):
    """The Other filter is a negation, and the easy way to write it (NOT IN the
    known sizes) silently sweeps up every NULL row, because NULL NOT IN (...)
    is not true but a naive Python-side filter would treat it as a match."""
    project_id = _project(client, alice)
    _task(project_id, "odd.jpg", 4032, 3024)
    _task(project_id, "null.jpg", None, None)
    _task(project_id, "full.jpg", *FULL)

    body = _get(client, alice, project_id, category="Other").json()
    assert [r["filename"] for r in body["items"]] == ["odd.jpg"]


def test_unknown_filter_selects_null_and_zero(client, alice):
    project_id = _project(client, alice)
    _task(project_id, "null.jpg", None, None)
    _task(project_id, "zero.jpg", 0, 0)
    _task(project_id, "full.jpg", *FULL)

    body = _get(client, alice, project_id, category="Unknown").json()
    assert sorted(r["filename"] for r in body["items"]) == ["null.jpg", "zero.jpg"]


def test_unknown_category_value_is_422(client, alice):
    project_id = _project(client, alice)
    res = _get(client, alice, project_id, category="Enormous")
    assert res.status_code == 422
    assert "Enormous" in res.json()["detail"]


def test_filename_search_is_a_case_insensitive_substring(client, alice):
    project_id = _project(client, alice)
    _task(project_id, "DSC_0001.jpg", *FULL)
    _task(project_id, "IMG_4471.jpg", *FULL)

    body = _get(client, alice, project_id, q="dsc").json()
    assert [r["filename"] for r in body["items"]] == ["DSC_0001.jpg"]


def test_search_and_category_combine(client, alice):
    project_id = _project(client, alice)
    _task(project_id, "site-a.jpg", *FULL)
    _task(project_id, "site-b.jpg", *HALF)

    body = _get(client, alice, project_id, q="site", category="Full").json()
    assert [r["filename"] for r in body["items"]] == ["site-a.jpg"]


# --- the summary -------------------------------------------------------------


def test_summary_describes_the_filtered_set_not_the_page(client, alice):
    """The summary strip is the number the user acts on. Computed from the page
    it would change as they paged, which is not a fact about the project."""
    project_id = _project(client, alice)
    for i in range(5):
        _task(project_id, f"f{i}.jpg", *FULL)
    for i in range(3):
        _task(project_id, f"h{i}.jpg", *HALF)

    body = _get(client, alice, project_id, page_size=2).json()
    assert len(body["items"]) == 2          # one page
    assert body["summary"]["total"] == 8    # but the whole set
    assert body["summary"]["by_category"]["Full"] == 5
    assert body["summary"]["by_category"]["Half"] == 3


def test_summary_lists_every_category_even_when_empty(client, alice):
    """A stable shape so the strip does not reflow as buckets empty."""
    project_id = _project(client, alice)
    _task(project_id, "full.jpg", *FULL)

    by_category = _get(client, alice, project_id).json()["summary"]["by_category"]
    assert set(by_category) == {"Full", "Half", "Other", "Unknown"}
    assert by_category["Half"] == 0


def test_summary_respects_the_category_filter(client, alice):
    project_id = _project(client, alice)
    _task(project_id, "full.jpg", *FULL)
    _task(project_id, "half.jpg", *HALF)

    body = _get(client, alice, project_id, category="Full").json()
    assert body["summary"]["total"] == 1
    assert body["summary"]["by_category"]["Half"] == 0


# --- file sizes --------------------------------------------------------------


def test_reports_the_real_file_size(client, alice):
    project_id = _project(client, alice)
    _task(project_id, "sized.jpg", *FULL, content=b"x" * 2048)

    body = _get(client, alice, project_id).json()
    assert body["items"][0]["size_bytes"] == 2048
    assert body["summary"]["total_bytes"] == 2048
    assert body["summary"]["total_bytes_partial"] is False


def test_missing_file_is_null_bytes_not_zero(client, alice):
    """A missing 6 MB original reported as '0 B' is a quietly wrong number in a
    report whose entire job is to be right about sizes."""
    project_id = _project(client, alice)
    _task(project_id, "gone.jpg", *FULL, content=b"temporary")
    db = SessionLocal()
    try:
        task = db.query(models.Task).filter(
            models.Task.project_id == project_id).first()
        os.remove(os.path.join(DATA_DIR, *task.image_path.split("/")))
    finally:
        db.close()

    body = _get(client, alice, project_id).json()
    assert body["items"][0]["size_bytes"] is None
    assert body["summary"]["missing_files"] == 1


def test_task_with_no_image_path_is_not_counted_as_missing_bytes(client, alice):
    project_id = _project(client, alice)
    _task(project_id, "pathless.jpg", *FULL)

    body = _get(client, alice, project_id).json()
    assert body["items"][0]["size_bytes"] is None


# --- paging and sorting ------------------------------------------------------


def test_sorts_by_filename_ascending_by_default(client, alice):
    project_id = _project(client, alice)
    for name in ("c.jpg", "a.jpg", "b.jpg"):
        _task(project_id, name, *FULL)

    body = _get(client, alice, project_id).json()
    assert [r["filename"] for r in body["items"]] == ["a.jpg", "b.jpg", "c.jpg"]


def test_sort_by_width_descending(client, alice):
    project_id = _project(client, alice)
    _task(project_id, "half.jpg", *HALF)
    _task(project_id, "full.jpg", *FULL)

    body = _get(client, alice, project_id, sort="width", order="desc").json()
    assert [r["filename"] for r in body["items"]] == ["full.jpg", "half.jpg"]


def test_unknown_sort_key_falls_back_rather_than_500s(client, alice):
    """The key is whitelisted, so an arbitrary column name from the query
    string can never reach ORDER BY."""
    project_id = _project(client, alice)
    _task(project_id, "a.jpg", *FULL)

    res = _get(client, alice, project_id, sort="password")
    assert res.status_code == 200
    assert res.json()["items"][0]["filename"] == "a.jpg"


def test_paging_is_stable_across_pages_when_rows_share_a_resolution(client, alice):
    """Every row here sorts equal on width, so without a total order the two
    pages could repeat and omit rows between requests."""
    project_id = _project(client, alice)
    for i in range(6):
        _task(project_id, f"img-{i}.jpg", *FULL)

    first = _get(client, alice, project_id, sort="width", page=1, page_size=3).json()
    second = _get(client, alice, project_id, sort="width", page=2, page_size=3).json()
    names = [r["filename"] for r in first["items"] + second["items"]]
    assert len(set(names)) == 6
    assert first["total_pages"] == 2


def test_empty_project_has_one_empty_page(client, alice):
    project_id = _project(client, alice)
    body = _get(client, alice, project_id).json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["total_pages"] == 1


def test_page_size_is_capped(client, alice):
    project_id = _project(client, alice)
    assert _get(client, alice, project_id, page_size=100000).status_code == 422


def test_does_not_leak_tasks_from_another_project(client, alice):
    mine = _project(client, alice, name="Mine")
    other = _project(client, alice, name="Other")
    _task(mine, "mine.jpg", *FULL)
    _task(other, "theirs.jpg", *FULL)

    body = _get(client, alice, mine).json()
    assert [r["filename"] for r in body["items"]] == ["mine.jpg"]


# --- rule 4: a GET must not write --------------------------------------------


def test_does_not_persist_dimensions_it_could_have_measured(client, alice):
    """A table that silently repaired rows as you paged would make the Unknown
    count depend on where you had browsed. Repair is the backfill script, run
    deliberately."""
    project_id = _project(client, alice)
    task_id = _task(project_id, "unmeasured.jpg", None, None, content=b"not-an-image")

    _get(client, alice, project_id)

    db = SessionLocal()
    try:
        task = db.query(models.Task).filter(models.Task.id == task_id).first()
        assert task.image_width is None
        assert task.image_height is None
    finally:
        db.close()
