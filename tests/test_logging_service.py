"""Unit tests for the service-log writer and its formatting.

These are the properties the whole logging redesign rests on: that a line
cannot be forged by user text, that files land in the right dated directory,
that a day boundary rotates, that retention deletes only what it should, and
that none of it can raise into a request.

See .devnotes/logging/02_PLAN.md §10.
"""
import datetime
import os

import pytest

import logging_service
from logging_service import (
    RequestContext,
    ServiceLogWriter,
    build_line,
    format_fields,
    sanitize_value,
)


@pytest.fixture
def writer(tmp_path):
    return ServiceLogWriter(base_dir=str(tmp_path / "service"))


def _ctx(**fields):
    ctx = RequestContext("abcd1234", 0.0)
    ctx.user = "kushal"
    ctx.fields = fields
    return ctx


# --- value sanitisation ---------------------------------------------------

def test_whitespace_and_equals_cannot_forge_a_field():
    """A user-supplied value must not be able to add keys or lines.

    The whole format is `k=v` separated by spaces, so a value carrying a space
    and an `=` would parse as an extra field, and one carrying a newline as an
    extra record. Task descriptions and class names are user text and reach the
    log, so this is a real input, not a hypothetical one.
    """
    forged = sanitize_value("a b=c\nevent=task.delete")
    assert " " not in forged
    assert "\n" not in forged
    assert "=" not in forged


def test_value_is_truncated_to_the_configured_cap():
    from config import LOG_VALUE_MAX
    out = sanitize_value("x" * (LOG_VALUE_MAX + 50))
    assert len(out) == LOG_VALUE_MAX + 1  # the cap plus the '~' marker
    assert out.endswith("~")


def test_none_and_empty_render_as_the_missing_marker():
    assert sanitize_value(None) == "-"
    assert sanitize_value("") == "-"


def test_booleans_render_as_words_not_python_repr():
    assert sanitize_value(True) == "true"
    assert sanitize_value(False) == "false"


def test_sensitive_keys_are_redacted_whatever_is_passed():
    out = format_fields({"password": "hunter2", "csrf_token": "abc", "task": 7})
    assert "hunter2" not in out
    assert "abc" not in out
    assert "task=7" in out


# --- line assembly --------------------------------------------------------

def test_line_carries_user_status_duration_and_event():
    ctx = _ctx(task=728, objects=403, objects_prev=417, delta=-14)
    ctx.event = "task.save"
    line = build_line(ctx, "POST", "/api/tasks", 200, 47, "192.168.1.5")
    assert "POST /api/tasks 200 47ms" in line
    assert "user=kushal" in line
    assert "req=abcd1234" in line
    assert "event=task.save" in line
    assert "delta=-14" in line


def test_level_defaults_from_status_when_no_event_set_one():
    assert " INFO " in build_line(_ctx(), "GET", "/api/tasks", 200, 3, "-")
    assert " WARN " in build_line(_ctx(), "GET", "/api/tasks", 403, 3, "-")
    assert " ERROR " in build_line(_ctx(), "GET", "/api/tasks", 500, 3, "-")


def test_unauthenticated_request_logs_a_dash_not_a_crash():
    ctx = RequestContext("deadbeef", 0.0)
    line = build_line(ctx, "POST", "/api/auth/token", 401, 9, "10.0.0.1")
    assert "user=-" in line


# --- file placement -------------------------------------------------------

def test_each_method_gets_its_own_file_in_the_days_directory(writer, tmp_path):
    day = datetime.datetime(2026, 9, 3, 10, 0, 0)
    writer.write("POST", "post line", now=day)
    writer.write("GET", "get line", now=day)
    writer.write("DELETE", "del line", now=day)

    base = tmp_path / "service" / "2026-09-03"
    assert (base / "POST.log").read_text(encoding="utf-8").strip() == "post line"
    assert (base / "GET.log").read_text(encoding="utf-8").strip() == "get line"
    assert (base / "DELETE.log").read_text(encoding="utf-8").strip() == "del line"


def test_unlisted_method_lands_in_other_not_dropped(writer, tmp_path):
    """An unexpected method must still be recorded somewhere.

    Silently dropping it would make the one request worth noticing the one
    request that leaves no trace.
    """
    writer.write("PUT", "put line", now=datetime.datetime(2026, 9, 3))
    assert (tmp_path / "service" / "2026-09-03" / "OTHER.log").exists()


def test_errors_are_duplicated_into_errors_log(writer, tmp_path):
    writer.write("POST", "boom", is_error=True, now=datetime.datetime(2026, 9, 3))
    base = tmp_path / "service" / "2026-09-03"
    assert "boom" in (base / "POST.log").read_text(encoding="utf-8")
    assert "boom" in (base / "errors.log").read_text(encoding="utf-8")


def test_a_new_day_opens_a_new_directory(writer, tmp_path):
    writer.write("POST", "monday", now=datetime.datetime(2026, 9, 3))
    writer.write("POST", "tuesday", now=datetime.datetime(2026, 9, 4))

    monday = (tmp_path / "service" / "2026-09-03" / "POST.log").read_text(encoding="utf-8")
    tuesday = (tmp_path / "service" / "2026-09-04" / "POST.log").read_text(encoding="utf-8")
    assert "monday" in monday and "tuesday" not in monday
    assert "tuesday" in tuesday and "monday" not in tuesday


def test_writes_are_flushed_so_a_crash_keeps_the_last_lines(writer, tmp_path):
    """Read back without closing the writer.

    Buffered, this file would be empty until the process exited — and the lines
    immediately before a crash are the ones the log exists for. The deploy box
    loses power with the app running (08_BACKUP_TRUNCATION.md).
    """
    writer.write("POST", "unflushed?", now=datetime.datetime(2026, 9, 3))
    path = tmp_path / "service" / "2026-09-03" / "POST.log"
    assert "unflushed?" in path.read_text(encoding="utf-8")


# --- retention ------------------------------------------------------------

def test_sweep_removes_only_directories_older_than_the_window(writer, tmp_path, monkeypatch):
    monkeypatch.setattr(logging_service, "LOG_RETENTION_DAYS", 30)
    base = tmp_path / "service"
    for name in ("2026-09-03", "2026-08-20", "2026-06-01", "notes"):
        (base / name).mkdir(parents=True)

    removed = writer.sweep_old(today=datetime.date(2026, 9, 3))

    assert removed == 1
    assert (base / "2026-09-03").exists()
    assert (base / "2026-08-20").exists()   # inside the 30-day window
    assert not (base / "2026-06-01").exists()
    # Anything that is not a dated directory is left alone: an operator may
    # have put it there and the sweep is not entitled to guess.
    assert (base / "notes").exists()


def test_sweep_on_a_missing_directory_is_a_no_op(tmp_path):
    writer = ServiceLogWriter(base_dir=str(tmp_path / "never-created"))
    assert writer.sweep_old() == 0


# --- failure containment --------------------------------------------------

def test_an_unwritable_directory_disables_logging_instead_of_raising(monkeypatch, tmp_path):
    writer = ServiceLogWriter(base_dir=str(tmp_path / "svc"))

    def boom(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(os, "makedirs", boom)
    writer.write("POST", "line")  # must not raise
    assert writer._disabled is True

    # And it stays quiet afterwards rather than retrying per request.
    writer.write("POST", "another")


def test_log_event_outside_a_request_is_a_no_op():
    """Scripts and background jobs call the same helpers as routes.

    `scripts/` imports router helpers, and background export/detect jobs run
    outside any request. Raising there would turn logging into the failure.
    """
    logging_service.log_event("task.save", task=1)  # no context bound


def test_log_event_merges_fields_and_keeps_the_highest_level():
    ctx = RequestContext("11112222", 0.0)
    token = logging_service.bind_context(ctx)
    try:
        logging_service.log_event("task.delete", level="WARN", task=5)
        logging_service.log_event("task.delete", objects=12)
        assert ctx.fields == {"task": 5, "objects": 12}
        # A later INFO detail must not demote a destructive event out of the
        # WARN trail.
        assert ctx.level == "WARN"
    finally:
        logging_service.reset_context(token)


# --- sampling -------------------------------------------------------------

def test_sampling_thins_repeats_but_never_drops_a_failure():
    sampler = logging_service._Sampler()
    assert sampler.allow("tab1|/heartbeat", 200, 1000.0) is True
    assert sampler.allow("tab1|/heartbeat", 200, 1001.0) is False
    # A different tab is sampled independently, so one busy client cannot
    # suppress everyone else's lines.
    assert sampler.allow("tab2|/heartbeat", 200, 1001.0) is True
    # Failures always survive: a failing heartbeat is the case anyone looking
    # at this file would be looking for.
    assert sampler.allow("tab1|/heartbeat", 403, 1001.0) is True


def test_sampling_never_drops_a_request_that_recorded_a_warning():
    sampler = logging_service._Sampler()
    assert sampler.allow("tab1|/api/team/time", 200, 1000.0) is True
    assert sampler.allow("tab1|/api/team/time", 200, 1001.0) is False
    # 200, but the handler recorded something notable (a silently dropped timer
    # delta). Thinning that away would lose the one line worth keeping.
    assert sampler.allow("tab1|/api/team/time", 200, 1001.0, level="WARN") is True


def test_skip_and_sample_path_classification():
    assert logging_service.should_skip("/health")
    assert logging_service.should_skip("/js/init.js")
    assert not logging_service.should_skip("/api/tasks")
    # A parameterised path: the sampled entry sits at the end, not the start.
    assert logging_service.is_sampled_path("/api/tasks/728/heartbeat")
    assert not logging_service.is_sampled_path("/api/tasks")
