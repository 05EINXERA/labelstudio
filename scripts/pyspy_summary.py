"""Turn a raw py-spy capture into the GIL budget this investigation is judged by.

Capture, from an elevated shell on the server (the app runs elevated):

    py-spy record -p <pid> -d 30 -r 100 --nonblocking --gil -f raw -o C:\\Temp\\pyspy\\profile.txt

then:

    python scripts/pyspy_summary.py C:\\Temp\\pyspy\\profile.txt
    python scripts/pyspy_summary.py before.txt after.txt     # side by side

`--gil` records only the thread holding the GIL at each sample, so the samples
are a direct budget of the one resource the single uvicorn process is limited
by: 30 s at 100 Hz is 3,000 possible samples, and `samples / 3000` is how busy
the GIL was. Each row below is the share of the *samples* whose stack passes
through a frame matching that pattern (inclusive, so rows overlap).

Read-only on the profile; never talks to the server. See
.devnotes/fix-performance-upgrade/01_PROFILE_ANALYSIS.md.
"""
import argparse
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# Raw py-spy joins frames with ';' and ends the line with the sample count.
Stack = Tuple[List[str], int]

_WORKER = "anyio" + chr(92) + "_backends" + chr(92) + "_asyncio.py:1033"


def _any(*needles: str) -> Callable[[str], bool]:
    return lambda frame: any(n in frame for n in needles)


# (label, predicate on one frame). A sample counts for a row when ANY frame in
# its stack satisfies the predicate. Kept in one table so before/after runs are
# always bucketed identically.
BUCKETS: List[Tuple[str, Callable[[str], bool]]] = [
    ("worker threads (sync endpoints)", lambda f: _WORKER in f),
    ("POST /api/tasks  (save)", _any("update_or_create_task")),
    ("  points compare / row kwargs", _any("dict_to_row_kwargs", "_points_equal")),
    ("  json.dumps (encoder)", _any("json" + chr(92) + "encoder.py")),
    ("  json.loads (decoder / orjson)", _any("json" + chr(92) + "decoder.py", "orjson")),
    ("  payload parse (_parsed)", _any("_parsed (")),
    ("  ORM row load (require_task)", _any("require_task")),
    ("  ORM instance loading", _any("orm" + chr(92) + "loading.py")),
    ("  commit", _any("commit_with_retry")),
    ("heartbeat", _any("heartbeat_task")),
    ("team time ping", _any("update_time_logged")),
    ("request body json (event loop)", _any("requests.py")),
    ("request gzip inflate (event loop)", _any("_inflate")),
    ("response gzip", _any("GZipResponder")),
    ("auth / jwt", _any("jwt", "get_current_user")),
]


def parse(text: str) -> List[Stack]:
    stacks: List[Stack] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            stack, count = line.rsplit(" ", 1)
            stacks.append((stack.split(";"), int(count)))
        except ValueError:
            continue
    return stacks


def budget(stacks: List[Stack], duration_s: float = 30.0, rate_hz: float = 100.0) -> Dict:
    """Total samples, GIL utilisation and the per-bucket sample shares."""
    total = sum(n for _, n in stacks)
    possible = duration_s * rate_hz
    rows: List[Tuple[str, int]] = []
    for label, predicate in BUCKETS:
        hit = sum(n for stack, n in stacks if any(predicate(f) for f in stack))
        rows.append((label, hit))
    return {
        "total": total,
        "gil_utilisation_pct": round(100 * total / possible, 1) if possible else 0.0,
        "rows": rows,
    }


def render(results: List[Tuple[str, Dict]]) -> str:
    lines = []
    header = f"{'':38}" + "".join(f"{name[:18]:>20}" for name, _ in results)
    lines.append(header)
    lines.append(f"{'GIL utilisation (of wall clock)':38}" + "".join(
        f"{r['gil_utilisation_pct']:>19.1f}%" for _, r in results))
    lines.append(f"{'samples':38}" + "".join(f"{r['total']:>20}" for _, r in results))
    lines.append("")
    for index, (label, _) in enumerate(BUCKETS):
        cells = []
        for _, result in results:
            hit = result["rows"][index][1]
            share = 100 * hit / result["total"] if result["total"] else 0.0
            cells.append(f"{share:>14.1f}% {hit:>4}")
        lines.append(f"{label:38}" + "".join(f"{c:>20}" for c in cells))
    lines.append("")
    lines.append("shares are of the samples in each capture (inclusive; rows overlap)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("profiles", nargs="+", type=Path)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--rate", type=float, default=100.0)
    args = parser.parse_args(argv)

    results = []
    for path in args.profiles:
        stacks = parse(path.read_text(encoding="utf-8", errors="replace"))
        results.append((path.name, budget(stacks, args.duration, args.rate)))
    print(render(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
