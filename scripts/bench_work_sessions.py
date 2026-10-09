"""Micro-benchmark for the work-session tracker's hot path (api.work_sessions.note_save).

The save request calls note_save after its commit, so its cost is added to every
save. This measures that call alone, from many threads at once, against the
lock-guarded dicts. It touches no database: a throwaway SQLite URL is pinned
before the app is imported so an inherited .env cannot point anything at the real
one.

    python scripts/bench_work_sessions.py [--threads 50] [--calls 20000]
"""
import argparse
import os
import sys
import tempfile
import threading
import time

_TMP = tempfile.mkdtemp(prefix="ws-bench-")
os.environ["DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "bench.db").replace(os.sep, "/")
os.environ.setdefault("JWT_SECRET", "bench-not-a-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from api import work_sessions  # noqa: E402


def _worker(thread_no: int, calls: int, out: list) -> None:
    samples = []
    note = work_sessions.note_save
    clock = time.perf_counter_ns
    for i in range(calls):
        t0 = clock()
        # A realistic mix: each thread is one user cycling through a few tasks.
        note(thread_no, f"user{thread_no}", i % 7, 30, i, i + 1)
        samples.append(clock() - t0)
    out.append(samples)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", type=int, default=50)
    parser.add_argument("--calls", type=int, default=20000)
    args = parser.parse_args()

    assert config.DATABASE_URL.startswith("sqlite") and _TMP.replace(os.sep, "/") in config.DATABASE_URL, config.DATABASE_URL
    config.MONITOR_ENABLED = True
    results: list = []
    threads = [threading.Thread(target=_worker, args=(n, args.calls, results)) for n in range(args.threads)]
    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - start

    samples = sorted(s for per_thread in results for s in per_thread)
    total = len(samples)

    def pct(p: float) -> float:
        return samples[min(total - 1, int(total * p))] / 1000.0

    print(f"threads={args.threads} calls/thread={args.calls} total={total} wall={wall:.2f}s")
    print(f"p50={pct(.50):.1f}us p95={pct(.95):.1f}us p99={pct(.99):.1f}us p99.9={pct(.999):.1f}us max={samples[-1] / 1000.0:.1f}us")
    print(f"live stretches={len(work_sessions._LIVE)} buffered closed={len(work_sessions._CLOSED)}")


if __name__ == "__main__":
    main()
