"""Images Info — `GET /api/projects/{id}/image-info`.

Plan: `.devnotes/image-size-check/01_PLAN.md`.

The feature's whole value is being *right* about what is in a project, so these
tests lean on the cases where a naive implementation would be quietly wrong
rather than loudly broken: an unmeasured image folded into the wrong bucket, a
missing file reported as zero bytes, a summary that describes the page instead
of the filtered set.
"""
import os
import uuid
from io import BytesIO

import pytest

import models
# Same route test_api_data_audit.py takes: the per-role cases need more than
# the three named user fixtures, and one fresh account per role is what keeps
# the grants from stacking.
from conftest import _register
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


def _grant(client, owner, member, project_id, role):
    """Give `member` exactly `role` on `project_id`, via a team of their own.

    A fresh team per call, so re-granting a different role in a loop replaces
    the caller's access rather than stacking a second grant — the resolver
    takes the maximum over grants, so reusing one team would leave the previous
    (possibly higher) role alive and silently pass a test that should fail.
    """
    team = client.post(
        "/api/teams", json={"name": f"T-{role}-{uuid.uuid4().hex[:6]}"}, headers=owner
    ).json()
    username = client.get("/api/auth/me", headers=member).json()["username"]
    client.post(
        f"/api/teams/{team['id']}/members",
        json={"username": username, "role": "member"}, headers=owner,
    )
    client.post(
        f"/api/projects/{project_id}/grants",
        json={"team_id": team["id"], "role": role}, headers=owner,
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


def test_owner_may_read_the_table(client, alice):
    project_id = _project(client, alice)
    _task(project_id, "a.jpg", *FULL)
    assert _get(client, alice, project_id).status_code == 200


@pytest.mark.parametrize("role", ["viewer", "annotator", "reviewer", "manager"])
def test_every_lesser_role_is_403(client, alice, role):
    """Owner-only, and stricter than Exports. A whole-project inventory is a
    management view: it answers 'what did we take delivery of', which is the
    owner's question, not 'what is in front of me'.

    403 rather than 404 because the caller has *a* role here — they need an
    actionable message naming what is required, not one implying the project is
    gone.

    One fresh user per role rather than re-granting to the same person: the
    resolver takes the maximum over a user's grants, so a loop that re-granted
    would leave the previous (higher) role alive and pass regardless."""
    project_id = _project(client, alice)
    _task(project_id, "a.jpg", *FULL)
    member = _register(client, f"member-{role}")
    _grant(client, alice, member, project_id, role)

    res = _get(client, member, project_id)
    assert res.status_code == 403
    assert "owner" in res.json()["detail"].lower()


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


# --- the spreadsheet ---------------------------------------------------------


def _xlsx(client, headers, project_id, **params):
    return client.get(
        f"/api/projects/{project_id}/image-info.xlsx", params=params, headers=headers
    )


def _load(res):
    from openpyxl import load_workbook
    return load_workbook(BytesIO(res.content))


@pytest.mark.parametrize("role", ["viewer", "annotator", "reviewer", "manager"])
def test_download_is_owner_gated_too(client, alice, role):
    """The same minimum as the table, deliberately. A view and its own download
    disagreeing about "may I?" is the split that leaves a half-open door behind
    after someone edits one of them."""
    project_id = _project(client, alice)
    _task(project_id, "a.jpg", *FULL)
    member = _register(client, f"dl-{role}")
    _grant(client, alice, member, project_id, role)

    assert _xlsx(client, member, project_id).status_code == 403
    # And the owner can.
    assert _xlsx(client, alice, project_id).status_code == 200


def test_download_with_no_role_is_404(client, alice, bob):
    """Same id-enumeration contract as the table."""
    project_id = _project(client, alice)
    assert _xlsx(client, bob, project_id).status_code == 404


def test_download_returns_a_real_workbook(client, alice):
    project_id = _project(client, alice)
    _task(project_id, "full.jpg", *FULL, content=b"x" * (2 * 1024 * 1024))
    _task(project_id, "half.jpg", *HALF, content=b"y" * 1024)

    res = _xlsx(client, alice, project_id)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "image-info-" in res.headers["content-disposition"]

    wb = _load(res)
    assert wb.sheetnames == ["Images", "Summary"]
    ws = wb["Images"]
    assert [c.value for c in ws[1]] == [
        "Filename", "Width", "Height", "Resolution",
        "Category", "Size (MB)", "Status", "Task ID",
    ]
    rows = {r[0]: r for r in ws.iter_rows(min_row=2, values_only=True)}
    assert rows["full.jpg"][1:5] == (5184, 3888, "5184x3888", "Full")
    assert rows["full.jpg"][5] == 2.0
    assert rows["half.jpg"][4] == "Half"


def test_size_is_a_number_so_the_column_can_be_summed(client, alice):
    """Text cells make the recipient's first instinct — select the column and
    read the sum — silently produce nothing."""
    project_id = _project(client, alice)
    _task(project_id, "a.jpg", *FULL, content=b"x" * 1024)

    ws = _load(_xlsx(client, alice, project_id))["Images"]
    assert isinstance(ws.cell(row=2, column=6).value, float)
    assert ws.cell(row=2, column=6).number_format == "0.00"


def test_header_is_frozen_and_filterable(client, alice):
    project_id = _project(client, alice)
    _task(project_id, "a.jpg", *FULL)

    ws = _load(_xlsx(client, alice, project_id))["Images"]
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref == "A1:H2"


def test_download_honours_the_active_filters(client, alice):
    """A download button under a filtered table means 'give me this'. One that
    silently returned everything would be discovered only after someone acted
    on it."""
    project_id = _project(client, alice)
    _task(project_id, "site-a-full.jpg", *FULL)
    _task(project_id, "site-b-half.jpg", *HALF)
    _task(project_id, "other-full.jpg", *FULL)

    ws = _load(_xlsx(client, alice, project_id, category="Full", q="site"))["Images"]
    names = [r[0] for r in ws.iter_rows(min_row=2, values_only=True)]
    assert names == ["site-a-full.jpg"]


def test_summary_sheet_totals_by_category(client, alice):
    project_id = _project(client, alice)
    for i in range(3):
        _task(project_id, f"f{i}.jpg", *FULL, content=b"x" * 1024)
    _task(project_id, "h.jpg", *HALF)

    ws = _load(_xlsx(client, alice, project_id))["Summary"]
    counts = {
        r[0]: r[1] for r in ws.iter_rows(min_row=6, values_only=True) if r[0]
    }
    assert counts["Full"] == 3
    assert counts["Half"] == 1
    assert counts["Other"] == 0
    assert counts["Total"] == 4


def test_sheet_name_survives_a_project_name_excel_rejects(client, alice):
    """Unhandled, []:*?/\\ produce a workbook Excel refuses to open — which the
    user would reasonably read as 'the export is broken'."""
    project_id = _project(client, alice, name="Site A / Roofs [2026]")
    _task(project_id, "a.jpg", *FULL)

    res = _xlsx(client, alice, project_id)
    assert res.status_code == 200
    assert _load(res).sheetnames == ["Images", "Summary"]
    # And the download filename carries nothing that could break the header.
    disposition = res.headers["content-disposition"]
    assert "/" not in disposition.split("filename=")[1]


def test_unmeasured_rows_export_blank_not_zero(client, alice):
    project_id = _project(client, alice)
    _task(project_id, "unknown.jpg", None, None)

    ws = _load(_xlsx(client, alice, project_id))["Images"]
    row = next(ws.iter_rows(min_row=2, values_only=True))
    assert row[1] is None and row[2] is None
    # An empty resolution cell, not "0x0" — openpyxl reads a blank back as
    # None. The point is that nothing fabricates a dimension nobody measured.
    assert not row[3]
    assert row[4] == "Unknown"


def test_empty_project_still_downloads(client, alice):
    """An empty workbook beats an error: 'no images match' is an answer."""
    project_id = _project(client, alice)
    res = _xlsx(client, alice, project_id)
    assert res.status_code == 200
    assert _load(res)["Images"].max_row == 1
