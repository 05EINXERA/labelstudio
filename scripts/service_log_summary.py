"""Summarise a window of the service log: load, latency and save shape.

The per-day service log (`<LOG_DIR>/service/YYYY-MM-DD/{GET,POST,PATCH,DELETE}.log`)
holds one line per request. This reads a time window of it and prints the
numbers every performance judgement here is made from, so a before/after
comparison is like-for-like:

    python scripts/service_log_summary.py                    # last 10 minutes of today
    python scripts/service_log_summary.py --minutes 30
    python scripts/service_log_summary.py --date 2026-10-01 --start 12:33 --end 12:43
    python scripts/service_log_summary.py --json

Read-only: it opens the log files for reading and never touches the database or
the running server. See .devnotes/fix-performance-upgrade/02_PLAN.md
(Measurement).

What to look at first: the **heartbeat** p50. A heartbeat carries no payload and
does almost no work, so its latency is pure queueing — it is the cleanest signal
for whether the server is saturated.
"""
import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional

_METHOD_FILES = ("GET.log", "POST.log", "PATCH.log", "DELETE.log")

_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2})?)\s+"
    r"(?P<level>\w+)\s+(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+(?P<status>\d{3})\s+"
    r"(?P<ms>\d+)ms(?P<rest>.*)$"
)
_KV = re.compile(r"(\w+)=(\S+)")
_ID = re.compile(r"/\d+")


def parse_line(line: str) -> Optional[dict]:
    """One log line as a record, or None when it is not a request line."""
    match = _LINE.match(line.rstrip("\n"))
    if not match:
        return None
    try:
        ts = datetime.datetime.fromisoformat(match.group("ts"))
    except ValueError:
        return None
    fields = dict(_KV.findall(match.group("rest")))
    path = match.group("path").split("?", 1)[0]
    return {
        "ts": ts,
        "method": match.group("method"),
        "path": path,
        "route": _ID.sub("/N", path),
        "status": int(match.group("status")),
        "ms": int(match.group("ms")),
        "fields": fields,
    }


def read_records(log_dir: Path) -> Iterator[dict]:
    """Every parsable request line in one day's directory."""
    for name in _METHOD_FILES:
        path = log_dir / name
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                record = parse_line(line)
                if record:
                    yield record


def percentile(values: List[float], fraction: float) -> float:
    """Nearest-rank percentile; 0 for an empty list."""
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction)))
    return ordered[index]


def _stats(latencies: List[int]) -> dict:
    if not latencies:
        return {"n": 0, "avg": 0, "p50": 0, "p90": 0, "p99": 0, "max": 0}
    return {
        "n": len(latencies),
        "avg": round(sum(latencies) / len(latencies)),
        "p50": percentile(latencies, 0.50),
        "p90": percentile(latencies, 0.90),
        "p99": percentile(latencies, 0.99),
        "max": max(latencies),
    }


def _int_field(record: dict, key: str) -> Optional[int]:
    try:
        return int(record["fields"][key])
    except (KeyError, ValueError):
        return None


def summarize(records: Iterable[dict], window_seconds: float) -> dict:
    """The window's load, latency and save shape.

    `window_seconds` is the length of the window the records were taken from,
    used for rates. Pure: no I/O, so it is testable on hand-built records.
    """
    records = list(records)
    window_seconds = max(window_seconds, 1.0)
    saves = [r for r in records if r["fields"].get("event") == "task.save"]
    heartbeats = [r for r in records if r["route"].endswith("/heartbeat")]
    per_route: Dict[str, List[int]] = defaultdict(list)
    for r in records:
        per_route[f'{r["method"]} {r["route"]}'].append(r["ms"])

    objects = [n for n in (_int_field(r, "objects") for r in saves) if n is not None]
    changed = Counter(r["fields"].get("changed") for r in saves)
    events = Counter(
        r["fields"]["event"]
        for r in records
        if r["fields"].get("event", "").startswith("task.save.")
    )
    users = {r["fields"].get("user") for r in saves if r["fields"].get("user")}

    saves_per_user: Counter = Counter(r["fields"].get("user") for r in saves)
    big = [r["ms"] for r in saves if (_int_field(r, "objects") or 0) > 1000]
    small = [r["ms"] for r in saves if (_int_field(r, "objects") or 0) <= 1000]

    return {
        "window_seconds": round(window_seconds),
        "requests": {**_stats([r["ms"] for r in records]),
                     "per_second": round(len(records) / window_seconds, 2)},
        "saves": {**_stats([r["ms"] for r in saves]),
                  "per_second": round(len(saves) / window_seconds, 2),
                  "users": len(users),
                  "avg_objects": round(sum(objects) / len(objects)) if objects else 0,
                  "max_objects": max(objects) if objects else 0,
                  "objects_per_second": round(sum(objects) / window_seconds),
                  "changed_true_pct": round(
                      100 * changed.get("true", 0) / len(saves)) if saves else 0,
                  "over_1000_objects": _stats(big),
                  "up_to_1000_objects": _stats(small),
                  "busiest_user_saves": max(saves_per_user.values(), default=0)},
        "heartbeat": _stats([r["ms"] for r in heartbeats]),
        "status": dict(Counter(str(r["status"]) for r in records)),
        "save_events": dict(events),
        "routes": {k: _stats(v) for k, v in
                   sorted(per_route.items(), key=lambda kv: -len(kv[1]))[:12]},
    }


def window_bounds(records: List[dict], start: Optional[str], end: Optional[str],
                  minutes: int):
    """(start, end) datetimes. `--start/--end` are HH:MM on the log's own day;
    otherwise the window is the last `minutes` before the final record."""
    if not records:
        return None, None
    last = max(r["ts"] for r in records)
    if end:
        hh, mm = (int(x) for x in end.split(":"))
        window_end = last.replace(hour=hh, minute=mm, second=0, microsecond=0)
    else:
        window_end = last
    if start:
        hh, mm = (int(x) for x in start.split(":"))
        window_start = window_end.replace(hour=hh, minute=mm, second=0, microsecond=0)
    else:
        window_start = window_end - datetime.timedelta(minutes=minutes)
    return window_start, window_end


def render(summary: dict, start, end) -> str:
    r, s, h = summary["requests"], summary["saves"], summary["heartbeat"]
    lines = [
        f"window {start:%Y-%m-%d %H:%M:%S} -> {end:%H:%M:%S} "
        f"({summary['window_seconds']} s)",
        "",
        f"requests   n={r['n']:<6} {r['per_second']}/s   avg {r['avg']} ms   "
        f"p50 {r['p50']}   p90 {r['p90']}   p99 {r['p99']}   max {r['max']}",
        f"saves      n={s['n']:<6} {s['per_second']}/s   avg {s['avg']} ms   "
        f"p50 {s['p50']}   p90 {s['p90']}   p99 {s['p99']}",
        f"           users={s['users']}  avg objects/save={s['avg_objects']}  "
        f"max={s['max_objects']}  objects/s={s['objects_per_second']}  "
        f"changed=true {s['changed_true_pct']}%",
        f"           >1000 objects: n={s['over_1000_objects']['n']} "
        f"avg {s['over_1000_objects']['avg']} ms   "
        f"<=1000: n={s['up_to_1000_objects']['n']} "
        f"avg {s['up_to_1000_objects']['avg']} ms",
        f"heartbeat  n={h['n']:<6} p50 {h['p50']} ms   p90 {h['p90']}   "
        f"(pure queueing signal)",
        f"status     {summary['status']}",
        f"save events{' ' if summary['save_events'] else ''}"
        f"{summary['save_events'] or ' none (no conflicts / refusals)'}",
        "",
        "busiest routes (n, avg ms, p50):",
    ]
    for name, st in summary["routes"].items():
        lines.append(f"  {st['n']:>6}  {st['avg']:>6}  {st['p50']:>6}  {name}")
    return "\n".join(lines)


def _default_log_dir() -> Path:
    base = os.environ.get("LOG_DIR") or os.path.join(
        os.environ.get("DATA_DIR", "D:/annotation-data"), "logs")
    return Path(base) / "service"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dir", type=Path, default=None,
                        help="service log root (default: $LOG_DIR/service)")
    parser.add_argument("--date", default=datetime.date.today().isoformat())
    parser.add_argument("--minutes", type=int, default=10)
    parser.add_argument("--start", help="HH:MM, on --date")
    parser.add_argument("--end", help="HH:MM, on --date (default: last record)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    day_dir = (args.dir or _default_log_dir()) / args.date
    if not day_dir.is_dir():
        print(f"no service log directory: {day_dir}", file=sys.stderr)
        return 2
    records = list(read_records(day_dir))
    start, end = window_bounds(records, args.start, args.end, args.minutes)
    if start is None:
        print("no request lines found", file=sys.stderr)
        return 2
    window = [r for r in records if start <= r["ts"] <= end]
    summary = summarize(window, (end - start).total_seconds())
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(render(summary, start, end))
    return 0


if __name__ == "__main__":
    sys.exit(main())
