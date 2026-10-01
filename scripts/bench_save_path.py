"""Micro-benchmark of the CPU a save (and a heartbeat) costs the server.

Builds a throw-away SQLite database, fills tasks of a few sizes with polygons
shaped like production's (45 vertices, ~2,300 shapes at the top end), and times
the pieces the live profile singled out -- on whichever tree it is run from, so
a before/after is one command in each:

    python scripts/bench_save_path.py
    python scripts/bench_save_path.py --sizes 150 950 2300 --repeat 7

It never touches the configured database: the engine URL is forced to a temp
file *before* the app modules are imported. SQLite is in-process, so the
numbers exclude Postgres and the network and measure the Python CPU that holds
the GIL -- which is the resource the server runs out of
(.devnotes/fix-performance-upgrade/01_PROFILE_ANALYSIS.md).

Reported per task size, best-of-N milliseconds:
    heartbeat    what `require_task` costs a request that never reads shapes
    parse        parsing the uploaded annotation string
    save (noop)  parse + diff of an unchanged set against the stored rows
    save (edit)  the same with one vertex moved, committed
"""
import argparse
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _shapes(rng, n, vertices=45):
    return [
        {"id": f"s{i}", "type": "polygon", "color": "#f00",
         "points": [{"x": rng.uniform(0, 3000), "y": rng.uniform(0, 3000)}
                    for _ in range(vertices)]}
        for i in range(n)
    ]


def _best(fn, repeat):
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return best * 1000


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[150, 950, 2300])
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args(argv)

    scratch = tempfile.mkdtemp(prefix="bench-save-")
    os.environ["DATABASE_URL"] = "sqlite:///" + scratch.replace("\\", "/") + "/bench.db"
    os.environ["DATA_DIR"] = scratch
    os.environ.setdefault("JWT_SECRET", "bench-not-a-secret")
    os.environ["APP_ENV"] = "development"
    sys.path.insert(0, str(_REPO))

    import models
    from database import Base, SessionLocal, engine
    from formats.annotation_rows import sync_task_annotations_for_project
    try:
        import fastjson
        parse = fastjson.loads
        parser_name = "orjson" if fastjson.HAVE_ORJSON else "stdlib (fastjson)"
    except ImportError:
        parse = json.loads
        parser_name = "stdlib json"

    Base.metadata.create_all(engine)
    rng = random.Random(1)
    db = SessionLocal()
    user = models.User(username="bench")
    db.add(user)
    db.flush()
    project = models.Project(name="bench", owner_id=user.id)
    db.add(project)
    db.commit()
    project_id = project.id
    db.close()

    print(f"parser: {parser_name}   repeat: best of {args.repeat}\n")
    print(f"{'shapes':>7} {'payload':>9} {'heartbeat':>10} {'parse':>8} "
          f"{'save noop':>10} {'save edit':>10}")
    for n in args.sizes:
        db = SessionLocal()
        task = models.Task(project_id=project_id, status="In Progress")
        db.add(task)
        db.flush()
        shapes = _shapes(rng, n)
        sync_task_annotations_for_project(db, task, shapes)
        db.commit()
        tid = task.id
        db.close()
        blob = json.dumps(shapes)

        def heartbeat():
            s = SessionLocal()
            s.get(models.Task, tid)
            s.close()

        def parse_only():
            parse(blob)

        def save_noop():
            s = SessionLocal()
            t = s.get(models.Task, tid)
            sync_task_annotations_for_project(s, t, parse(blob))
            s.rollback()
            s.close()

        def save_edit():
            s = SessionLocal()
            t = s.get(models.Task, tid)
            incoming = parse(blob)
            incoming[0]["points"][0]["x"] += 1.0
            sync_task_annotations_for_project(s, t, incoming)
            s.rollback()          # keep the stored set identical for every repeat
            s.close()

        print(f"{n:>7} {len(blob) / 1e6:>7.2f}MB "
              f"{_best(heartbeat, args.repeat):>8.1f}ms {_best(parse_only, args.repeat):>6.1f}ms "
              f"{_best(save_noop, args.repeat):>8.1f}ms {_best(save_edit, args.repeat):>8.1f}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
