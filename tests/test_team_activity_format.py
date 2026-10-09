"""The Activity view's pure formatter, run under node (skipped without it)."""
import json
import os
import shutil
import subprocess

import pytest

FORMAT_JS = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "frontend", "js", "pages", "team", "activity-format.js",
)
EM = "—"


def _run(expression):
    if shutil.which("node") is None:
        pytest.skip("node is not available on PATH")
    url = "file:///" + FORMAT_JS.replace("\\", "/")
    script = f"import('{url}').then(m => console.log(JSON.stringify({expression})));"
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout.strip())


def test_duration():
    assert _run("[0, 45, 60, 1830, 3599, 3900, 7200].map(m.formatDuration)") == [
        "0s", "45s", "1m 00s", "30m 30s", "59m 59s", "1h 05m", "2h 00m",
    ]
    assert _run("[null, -5, 'x'].map(m.formatDuration)") == ["0s", "0s", "0s"]


def test_count_never_shows_zero_for_unknown():
    assert _run("[null, undefined, 0, 7].map(m.formatCount)") == [EM, EM, "0", "7"]


def test_delta():
    assert _run("[m.formatDelta(5, 24), m.formatDelta(5, 5), m.formatDelta(9, 6)]") == ["+19", "0", "-3"]
    assert _run("[m.formatDelta(null, 5), m.formatDelta(5, null), m.formatDelta(undefined, undefined)]") == [EM] * 3


def test_clock_applies_the_offset_including_the_345_case():
    # 18:30Z is 00:15 the next day at UTC+05:45.
    got = _run("[m.formatClock('2026-10-09T18:30:00Z', 345), m.formatClock('2026-10-09T18:30:00Z', 0),"
               " m.formatDayClock('2026-10-09T18:30:00Z', 345), m.formatClock('nonsense', 0)]")
    assert got == ["00:15", "18:30", "10 Oct 00:15", EM]


def test_period_shows_dates_only_across_local_days():
    got = _run("[m.formatPeriod('2026-10-09T08:00:00Z', '2026-10-09T10:05:00Z', 0),"
               " m.formatPeriod('2026-10-09T18:00:00Z', '2026-10-09T19:00:00Z', 345)]")
    assert got[0] == "08:00 – 10:05"
    assert got[1] == "09 Oct 23:45 – 10 Oct 00:45"


def test_sessions_label():
    assert _run("[1, 2, 0].map(m.sessionsLabel)") == ["1 session", "2 sessions", "0 sessions"]


def test_empty_message_distinguishes_four_situations():
    got = _run(
        "[m.emptyMessage({error: true}), m.emptyMessage({enabled: false}),"
        " m.emptyMessage({enabled: true, monitoringSince: null}),"
        " m.emptyMessage({enabled: true, monitoringSince: '2026-10-01T00:00:00Z', filtered: true}),"
        " m.emptyMessage({enabled: true, monitoringSince: '2026-10-01T00:00:00Z', offsetMinutes: 0})]"
    )
    assert len(set(got)) == 5
    assert "off" in got[1] and "recorded yet" in got[2] and "01 Oct 00:00" in got[4]
