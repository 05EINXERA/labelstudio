"""Micro-benchmark of the work-session tracker's request-path cost (T0.2).

Hammers note_time/note_save from many threads and reports per-call latency, to
check the 'dict ops under a lock, nothing else' claim with numbers rather than
belief (CLAUDE.md "How to work", rule C). No database is touched.

    python scripts/bench_work_sessions.py --threads 50 --calls 20000
"""
import argparse
import os
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

config.MONITOR_ENABLED = True
from api import work_sessions as ws  # noqa: E402


def worker(n, uid, out):
    lat = []
    for i in range(n):
        t = time.perf_counter()
        if i % 3:
            ws.note_time(uid, i % 5, 30)
        else:
            ws.note_save(uid, i % 5, i, i + 1)
        lat.append(time.perf_counter() - t)
    out.append(lat)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=50)
    ap.add_argument("--calls", type=int, default=20000)
    a = ap.parse_args()
    out = []
    threads = [
        threading.Thread(target=worker, args=(a.calls, u, out)) for u in range(a.threads)
    ]
    start = time.perf_counter()
    [t.start() for t in threads]
    [t.join() for t in threads]
    wall = time.perf_counter() - start
    allv = sorted(x for lat in out for x in lat)
    us = lambda q: allv[min(len(allv) - 1, int(len(allv) * q))] * 1e6
    print(f"calls={len(allv)} wall={wall:.2f}s")
    print(f"mean={statistics.mean(allv)*1e6:.1f}us p50={us(.5):.1f}us p99={us(.99):.1f}us p99.9={us(.999):.1f}us max={allv[-1]*1e6:.0f}us")


if __name__ == "__main__":
    main()
