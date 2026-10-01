"""The two measurement scripts used to judge every performance change.

`scripts/service_log_summary.py` and `scripts/pyspy_summary.py` are what the
before/after comparisons in .devnotes/fix-performance-upgrade/ are made with, so
they must bucket and count identically from one run to the next. Both are pure
at their core and are tested here on hand-built input, with no log directory
and no running server.
"""
import datetime
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


log = _load("service_log_summary")
spy = _load("pyspy_summary")

SAVE = (
    "2026-10-01T12:43:05.979+05:45 INFO  POST /api/tasks 200 {ms}ms user={user} "
    "ip=192.168.110.69 req=290d0587 event=task.save task=725 project=264 "
    "objects={objects} objects_prev={objects} objects_client={objects} delta=0 "
    "status_from=In_Progress status_to=In_Progress client=c1 time_delta=7 "
    "changed={changed}"
)
BEAT = (
    "2026-10-01T12:43:06.100+05:45 INFO  POST /api/tasks/725/heartbeat 200 "
    "{ms}ms user=u ip=1.2.3.4 req=abc"
)


def _record(template, **kw):
    record = log.parse_line(template.format(**kw))
    assert record is not None
    return record


# --- service_log_summary ----------------------------------------------------

def test_parse_line_reads_fields_and_normalises_ids():
    r = _record(SAVE, ms=4593, user="siwani", objects=147, changed="true")
    assert r["method"] == "POST"
    assert r["route"] == "/api/tasks"
    assert r["status"] == 200 and r["ms"] == 4593
    assert r["fields"]["event"] == "task.save"
    assert r["fields"]["objects"] == "147"
    assert _record(BEAT, ms=5)["route"] == "/api/tasks/N/heartbeat"


def test_parse_line_rejects_non_request_lines():
    assert log.parse_line("") is None
    assert log.parse_line("2026-10-01 12:00:00,000 INFO  [-] main: started") is None


def test_percentile_is_nearest_rank_and_safe_on_empty():
    assert log.percentile([], 0.5) == 0
    assert log.percentile([10], 0.99) == 10
    assert log.percentile(list(range(1, 101)), 0.5) == 51
    assert log.percentile(list(range(1, 101)), 0.9) == 91


def test_summarize_counts_saves_objects_and_heartbeats():
    records = (
        [_record(SAVE, ms=1000, user="a", objects=100, changed="true")] * 3
        + [_record(SAVE, ms=3000, user="b", objects=2000, changed="false")]
        + [_record(BEAT, ms=40), _record(BEAT, ms=60)]
    )
    s = log.summarize(records, window_seconds=10)
    assert s["requests"]["n"] == 6
    assert s["requests"]["per_second"] == 0.6
    assert s["saves"]["n"] == 4 and s["saves"]["users"] == 2
    assert s["saves"]["avg_objects"] == round((300 + 2000) / 4)
    assert s["saves"]["max_objects"] == 2000
    assert s["saves"]["changed_true_pct"] == 75
    assert s["saves"]["over_1000_objects"]["n"] == 1
    assert s["saves"]["up_to_1000_objects"]["n"] == 3
    assert s["heartbeat"]["n"] == 2 and s["heartbeat"]["p50"] == 60


def test_summarize_empty_window_does_not_divide_by_zero():
    s = log.summarize([], window_seconds=0)
    assert s["requests"]["n"] == 0 and s["saves"]["avg_objects"] == 0


def test_window_bounds_default_is_last_n_minutes():
    records = [_record(BEAT, ms=1)]
    start, end = log.window_bounds(records, None, None, minutes=10)
    assert end - start == datetime.timedelta(minutes=10)
    start, end = log.window_bounds(records, "12:33", "12:43", minutes=10)
    assert (start.hour, start.minute, end.hour, end.minute) == (12, 33, 12, 43)


def test_main_reads_a_day_directory(tmp_path, capsys):
    day = tmp_path / "2026-10-01"
    day.mkdir()
    (day / "POST.log").write_text(
        "\n".join([
            SAVE.format(ms=2000, user="a", objects=50, changed="true"),
            BEAT.format(ms=30),
        ]) + "\n",
        encoding="utf-8",
    )
    code = log.main(["--dir", str(tmp_path), "--date", "2026-10-01", "--json",
                     "--minutes", "5"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["saves"]["n"] == 1 and summary["heartbeat"]["n"] == 1


def test_main_reports_a_missing_directory(tmp_path, capsys):
    assert log.main(["--dir", str(tmp_path), "--date", "1999-01-01"]) == 2


# --- pyspy_summary ----------------------------------------------------------

BS = chr(92)
WORKER = f"run (anyio{BS}_backends{BS}_asyncio.py:1033)"
RAW = "\n".join([
    # a save spending its time in json.dumps under dict_to_row_kwargs
    f"_bootstrap (threading.py:1032);{WORKER};update_or_create_task (tasks.py:1584);"
    f"sync_task_annotations (annotation_rows.py:242);dict_to_row_kwargs (annotation_rows.py:110);"
    f"dumps (json{BS}__init__.py:231);iterencode (json{BS}encoder.py:258) 6",
    # a save in the payload parse
    f"_bootstrap (threading.py:1032);{WORKER};update_or_create_task (tasks.py:1448);"
    f"_parsed (tasks.py:164);raw_decode (json{BS}decoder.py:354) 3",
    # a heartbeat paying for the eager row load
    f"_bootstrap (threading.py:1032);{WORKER};heartbeat_task (tasks.py:1173);"
    f"require_task (permissions.py:241);_instance (sqlalchemy{BS}orm{BS}loading.py:1108) 1",
])


def test_pyspy_parse_and_budget():
    stacks = spy.parse(RAW + "\n\nnot a stack line\n")
    assert sum(n for _, n in stacks) == 10
    result = spy.budget(stacks, duration_s=1, rate_hz=20)
    assert result["total"] == 10
    assert result["gil_utilisation_pct"] == 50.0
    rows = dict(result["rows"])
    assert rows["POST /api/tasks  (save)"] == 9
    assert rows["  json.dumps (encoder)"] == 6
    assert rows["  payload parse (_parsed)"] == 3
    assert rows["heartbeat"] == 1
    assert rows["  ORM row load (require_task)"] == 1
    assert rows["worker threads (sync endpoints)"] == 10


def test_pyspy_render_compares_two_captures():
    before = spy.budget(spy.parse(RAW), 1, 20)
    after = spy.budget(spy.parse(RAW.splitlines()[1]), 1, 20)
    text = spy.render([("before.txt", before), ("after.txt", after)])
    assert "before.txt" in text and "after.txt" in text
    assert "GIL utilisation" in text


def test_pyspy_main_reads_files(tmp_path, capsys):
    profile = tmp_path / "p.txt"
    profile.write_text(RAW, encoding="utf-8")
    assert spy.main([str(profile)]) == 0
    assert "POST /api/tasks" in capsys.readouterr().out
