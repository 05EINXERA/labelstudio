"""R7 — the attendance workbook and CSV.

The assertion that carries the most weight is that **durations come back as
numbers, not strings**. `api/routers/image_info.py:415-417` records exactly
this failure: "the recipient's first instinct is to select the column and read
the sum, and text cells make that silently produce nothing." A file that looks
right and sums to nothing is worse than one that looks wrong.

The second is that the file **says which instance produced it**. The two
deployments are never merged (Q2), so manual comparison of exports is the only
cross-instance mechanism there is — an unidentified file cannot do that job.
"""
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

import pytest

import config
import models
from api import attendance, attendance_export
from api import attendance_report as report
from database import SessionLocal

openpyxl = pytest.importorskip("openpyxl")


LOCAL_DAY = date(2026, 9, 20)


@pytest.fixture(autouse=True)
def _clean_buffer():
    attendance.reset_for_tests()
    original = config.ATTENDANCE_ENABLED
    config.ATTENDANCE_ENABLED = True
    yield
    config.ATTENDANCE_ENABLED = original
    attendance.reset_for_tests()


def _row(username="anna", user_id=1, **overrides):
    """One admin-table row, in the shape `_collect` produces."""
    base = {
        "user_id": user_id,
        "username": username,
        "local_date": LOCAL_DAY,
        "first_seen": datetime(2026, 9, 20, 4, 5, tzinfo=timezone.utc),
        "last_seen": datetime(2026, 9, 20, 12, 20, tzinfo=timezone.utc),
        "last_seen_reason": "logout",
        "session_count": 2,
        "present_seconds": 27000,     # 7.5 h
        "break_seconds": 2700,        # 0.75 h
        "manual_break_seconds": 900,  # 0.25 h
        "active_seconds": 21600,      # 6.0 h
        "tasks_touched": 14,
        "tasks_reviewed": 2,
        "has_unended_break": False,
    }
    base.update(overrides)
    return base


def _load(content):
    return openpyxl.load_workbook(BytesIO(content))


# --- Durations as numbers ---------------------------------------------------


def test_durations_come_back_as_numbers_not_strings():
    """THE export test. A text duration silently breaks SUM and pivots."""
    wb = _load(attendance_export.build_workbook([_row()], LOCAL_DAY, LOCAL_DAY))
    daily = wb["Daily"]

    headers = [c.value for c in daily[1]]
    for label in ("Present (h)", "Active timer (h)", "Breaks (h)"):
        col = headers.index(label) + 1
        value = daily.cell(row=2, column=col).value
        assert isinstance(value, (int, float)), (
            f"{label} came back as {type(value).__name__} ({value!r}); a text "
            "duration makes SUM silently produce nothing"
        )


def test_durations_are_decimal_hours_with_the_right_value():
    wb = _load(attendance_export.build_workbook([_row()], LOCAL_DAY, LOCAL_DAY))
    daily = wb["Daily"]
    headers = [c.value for c in daily[1]]

    assert daily.cell(row=2, column=headers.index("Present (h)") + 1).value == 7.5
    assert daily.cell(row=2, column=headers.index("Active timer (h)") + 1).value == 6.0
    assert daily.cell(row=2, column=headers.index("Breaks (h)") + 1).value == 0.75
    assert daily.cell(row=2, column=headers.index("Entered later (h)") + 1).value == 0.25


def test_a_short_interval_does_not_round_to_a_bare_integer_zero():
    """Rounded to 2 places a 30-second break is 0.0, which openpyxl writes as
    the integer 0 — mixing ints and floats in a numeric column and reading as
    "no time at all" rather than "very little"."""
    wb = _load(attendance_export.build_workbook(
        [_row(break_seconds=30)], LOCAL_DAY, LOCAL_DAY
    ))
    daily = wb["Daily"]
    headers = [c.value for c in daily[1]]
    value = daily.cell(row=2, column=headers.index("Breaks (h)") + 1).value
    assert value > 0, "a 30-second break rounded away to zero"
    assert isinstance(value, float)


def test_duration_columns_carry_an_explicit_number_format():
    """So an empty cell reads 0.00 rather than a bare 0."""
    wb = _load(attendance_export.build_workbook([_row()], LOCAL_DAY, LOCAL_DAY))
    daily = wb["Daily"]
    headers = [c.value for c in daily[1]]
    col = headers.index("Present (h)") + 1
    assert daily.cell(row=2, column=col).number_format == "0.00"


def test_dates_are_real_dates_so_sorting_works():
    wb = _load(attendance_export.build_workbook([_row()], LOCAL_DAY, LOCAL_DAY))
    daily = wb["Daily"]
    headers = [c.value for c in daily[1]]
    value = daily.cell(row=2, column=headers.index("Date") + 1).value
    assert isinstance(value, (date, datetime)), (
        f"Date came back as {type(value).__name__}; sorting and filtering break"
    )


def test_counts_stay_integers():
    wb = _load(attendance_export.build_workbook([_row()], LOCAL_DAY, LOCAL_DAY))
    daily = wb["Daily"]
    headers = [c.value for c in daily[1]]
    assert daily.cell(row=2, column=headers.index("Tasks touched") + 1).value == 14
    assert daily.cell(row=2, column=headers.index("Tasks reviewed") + 1).value == 2


# --- The file identifies itself ---------------------------------------------


def test_the_summary_names_the_instance_range_and_timezone():
    """Q2: manual comparison of exports is the only cross-instance mechanism,
    so a file that does not say where it came from cannot do its job."""
    wb = _load(attendance_export.build_workbook(
        [_row()], date(2026, 9, 1), date(2026, 9, 30)
    ))
    text = "\n".join(
        str(cell.value)
        for row in wb["Summary"].iter_rows()
        for cell in row
        if cell.value is not None
    )
    assert config.ATTENDANCE_INSTANCE_ID in text
    assert config.ATTENDANCE_TZ in text
    assert "2026-09-01" in text
    assert "2026-09-30" in text


def test_the_summary_is_the_first_sheet():
    """It is the half that gets read and the half that gets pasted."""
    wb = _load(attendance_export.build_workbook([_row()], LOCAL_DAY, LOCAL_DAY))
    assert wb.sheetnames[0] == "Summary"
    assert wb.sheetnames == ["Summary", "Daily", "Sessions"]


def test_the_summary_carries_the_qualifications():
    """These columns mean something narrower than their names. The file is
    opened by people who never saw the dashboard's footnote."""
    wb = _load(attendance_export.build_workbook([_row()], LOCAL_DAY, LOCAL_DAY))
    text = " ".join(
        str(cell.value)
        for row in wb["Summary"].iter_rows()
        for cell in row
        if cell.value is not None
    ).lower()
    assert "excludes declared breaks" in text
    assert "lower bound" in text
    assert "not tasks completed" in text or "not tasks" in text


def test_the_daily_sheet_names_the_instance_per_row():
    wb = _load(attendance_export.build_workbook([_row()], LOCAL_DAY, LOCAL_DAY))
    daily = wb["Daily"]
    headers = [c.value for c in daily[1]]
    value = daily.cell(row=2, column=headers.index("Instance") + 1).value
    assert value == config.ATTENDANCE_INSTANCE_ID


# --- Labelling --------------------------------------------------------------


def test_a_timeout_is_never_labelled_as_a_logout():
    wb = _load(attendance_export.build_workbook(
        [_row(last_seen_reason="timeout")], LOCAL_DAY, LOCAL_DAY
    ))
    daily = wb["Daily"]
    headers = [c.value for c in daily[1]]
    value = daily.cell(row=2, column=headers.index("End reason") + 1).value
    assert "timeout" in value
    assert "logged out" not in value


def test_no_column_claims_tasks_were_completed():
    """Per-user completion is not derivable — no author on the annotation
    write path — so no header may imply it."""
    labels = [label for label, _ in attendance_export.DAILY_COLUMNS]
    labels += [label for label, _ in attendance_export.SESSION_COLUMNS]
    assert not [l for l in labels if "complet" in l.lower()]
    assert "Tasks touched" in labels


def test_the_workbook_has_a_frozen_header_and_a_filter():
    """So the file is a working document rather than a printout."""
    wb = _load(attendance_export.build_workbook([_row()], LOCAL_DAY, LOCAL_DAY))
    daily = wb["Daily"]
    assert daily.freeze_panes == "A2"
    assert daily.auto_filter.ref


# --- The Sessions sheet -----------------------------------------------------


def test_the_sessions_sheet_carries_break_detail():
    sessions = {
        (1, LOCAL_DAY): [{
            "started_at": datetime(2026, 9, 20, 4, 5, tzinfo=timezone.utc),
            "ended_at": datetime(2026, 9, 20, 12, 20, tzinfo=timezone.utc),
            "end_reason": "logout",
            "seconds": 27000,
            "span_seconds": 29700,
            "break_seconds": 2700,
            "tasks_touched": 14,
            "breaks": [{
                "started_at": datetime(2026, 9, 20, 7, 15, tzinfo=timezone.utc),
                "ended_at": datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc),
                "seconds": 2700,
                "ended": True,
                "source": "manual",
                "entered_at": None,
            }],
        }]
    }
    wb = _load(attendance_export.build_workbook(
        [_row()], LOCAL_DAY, LOCAL_DAY, sessions_by_key=sessions
    ))
    sheet = wb["Sessions"]
    assert sheet.max_row == 2
    detail = str(sheet.cell(row=2, column=9).value)
    assert "entered later" in detail, "a manual break must be marked"


def test_an_unended_break_is_marked_in_the_sessions_sheet():
    sessions = {
        (1, LOCAL_DAY): [{
            "started_at": datetime(2026, 9, 20, 4, 5, tzinfo=timezone.utc),
            "ended_at": datetime(2026, 9, 20, 5, 0, tzinfo=timezone.utc),
            "end_reason": "timeout",
            "seconds": 3300,
            "span_seconds": 3300,
            "break_seconds": 0,
            "tasks_touched": 1,
            "breaks": [{
                "started_at": datetime(2026, 9, 20, 5, 0, tzinfo=timezone.utc),
                "ended_at": datetime(2026, 9, 20, 5, 0, tzinfo=timezone.utc),
                "seconds": 0,
                "ended": False,
                "source": "declared",
                "entered_at": None,
            }],
        }]
    }
    wb = _load(attendance_export.build_workbook(
        [_row()], LOCAL_DAY, LOCAL_DAY, sessions_by_key=sessions
    ))
    detail = str(wb["Sessions"].cell(row=2, column=9).value)
    assert "not ended" in detail


def test_an_empty_range_still_produces_a_valid_workbook():
    wb = _load(attendance_export.build_workbook([], LOCAL_DAY, LOCAL_DAY))
    assert wb.sheetnames == ["Summary", "Daily", "Sessions"]
    assert wb["Daily"].max_row == 1  # the header alone


# --- CSV --------------------------------------------------------------------


def test_the_csv_identifies_the_instance_and_timezone():
    text = attendance_export.build_csv([_row()], LOCAL_DAY, LOCAL_DAY)
    assert config.ATTENDANCE_INSTANCE_ID in text
    assert config.ATTENDANCE_TZ in text


def test_the_csv_writes_decimal_hours_not_formatted_strings():
    text = attendance_export.build_csv([_row()], LOCAL_DAY, LOCAL_DAY)
    data_line = [
        line for line in text.splitlines()
        if line and not line.startswith("#") and not line.startswith("Person")
    ][0]
    assert "7.5" in data_line
    assert "7h" not in data_line, "a formatted duration cannot be summed"


def test_the_csv_header_matches_the_daily_sheet():
    text = attendance_export.build_csv([_row()], LOCAL_DAY, LOCAL_DAY)
    header = [
        line for line in text.splitlines() if line.startswith("Person")
    ][0]
    for label, _ in attendance_export.DAILY_COLUMNS:
        assert label in header


# --- The endpoints ----------------------------------------------------------


def _user_id(client, headers):
    return client.get("/api/auth/me", headers=headers).json()["id"]


def _make_admin(user_id, admin=True):
    db = SessionLocal()
    try:
        db.get(models.User, user_id).is_admin = admin
        db.commit()
    finally:
        db.close()


@pytest.fixture
def admin(client, alice):
    _make_admin(_user_id(client, alice))
    return alice


@pytest.mark.parametrize("path", ["export.xlsx", "export.csv"])
def test_export_is_admin_only(client, bob, path):
    """Stricter than the codebase's `reviewer` bar for dataset exports,
    deliberately: this is personal data about every employee (Q20)."""
    res = client.get(
        f"/api/attendance/{path}?from=2026-09-01&to=2026-09-02", headers=bob
    )
    assert res.status_code == 404


@pytest.mark.parametrize("path", ["export.xlsx", "export.csv"])
def test_export_requires_authentication(client, path):
    res = client.get(f"/api/attendance/{path}?from=2026-09-01&to=2026-09-02")
    assert res.status_code == 401


def test_xlsx_export_downloads_a_readable_workbook(client, admin):
    res = client.get(
        "/api/attendance/export.xlsx?from=2026-09-01&to=2026-09-02", headers=admin
    )
    assert res.status_code == 200
    assert "spreadsheetml" in res.headers["content-type"]
    assert "attachment" in res.headers["content-disposition"]
    wb = _load(res.content)
    assert wb.sheetnames == ["Summary", "Daily", "Sessions"]


def test_csv_export_downloads_as_a_bare_file(client, admin):
    res = client.get(
        "/api/attendance/export.csv?from=2026-09-01&to=2026-09-02", headers=admin
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]


def test_the_filename_names_the_instance_and_range(client, admin):
    """Two exports being compared land in one folder together."""
    res = client.get(
        "/api/attendance/export.csv?from=2026-09-01&to=2026-09-02", headers=admin
    )
    disposition = res.headers["content-disposition"]
    assert "attendance-" in disposition
    assert "2026-09-01" in disposition
    assert "2026-09-02" in disposition


@pytest.mark.parametrize("path", ["export.xlsx", "export.csv"])
def test_the_export_range_is_bounded_server_side(client, admin, path):
    res = client.get(
        f"/api/attendance/{path}?from=2026-01-01&to=2026-12-31", headers=admin
    )
    assert res.status_code == 400
    assert "maximum" in res.json()["detail"].lower()


def test_a_reversed_export_range_is_refused(client, admin):
    res = client.get(
        "/api/attendance/export.csv?from=2026-09-30&to=2026-09-01", headers=admin
    )
    assert res.status_code == 400


def test_a_zero_duration_still_renders_as_two_decimal_places():
    """Found on real data, not by these fixtures.

    openpyxl narrows `float(0.0)` to an integer on write, so a zero total
    renders as a bare "0" beside a neighbouring "0.25" unless the *format*
    carries it — the cell type cannot. This is the point `image_info.py`
    records, and it only shows up on a day where someone's present time is
    genuinely zero.
    """
    row = _row(present_seconds=0, active_seconds=0, break_seconds=900,
               manual_break_seconds=900)
    wb = _load(attendance_export.build_workbook([row], LOCAL_DAY, LOCAL_DAY))

    daily = wb["Daily"]
    headers = [c.value for c in daily[1]]
    for label in ("Present (h)", "Active timer (h)", "Breaks (h)"):
        cell = daily.cell(row=2, column=headers.index(label) + 1)
        assert cell.number_format == "0.00", (
            f"Daily {label} has format {cell.number_format!r}; a zero would "
            "render as a bare 0 beside its neighbours"
        )

    summary = wb["Summary"]
    checked = 0
    for i in range(1, summary.max_row + 1):
        label = summary.cell(row=i, column=1).value
        if label and "(h)" in str(label):
            cell = summary.cell(row=i, column=2)
            assert cell.number_format == "0.00", (
                f"Summary {label!r} has format {cell.number_format!r}"
            )
            checked += 1
    assert checked == 4, f"expected 4 duration stats on Summary, found {checked}"
