"""Load test: N shared-account annotators against a running server.

Simulates the office shift pattern the deployment must survive (audit L1/L3/L8):

  1. Shift start — every client fetches the annotation-free task list at once
     (the "mass refresh" burst).
  2. Steady state — each client repeatedly: opens a task (GET /api/tasks/{id}),
     claims it, saves annotations a few times with heartbeats in between,
     releases, moves to the next task.
  3. Occasional class import / re-upload is left out on purpose — those are
     operator actions, not per-annotator, and would drown the signal we want
     (steady annotate+save latency under concurrency).

All clients share ONE login, matching the real deployment. Auth is by bearer
token in the Authorization header, which `require_csrf` exempts — so the
harness does not need the browser CSRF dance, and this still exercises the same
DB write paths, the soft lock, and the conflict logic.

This talks to a *running* server over HTTP; it does not import the app. Point it
at the real deployment (or a local `uvicorn main:app`). It only READS and writes
to a project it creates itself, so it will not touch real annotation data —
but run it against a disposable database, not production.

Usage:
    python scripts/loadtest/shift_sim.py --base-url http://127.0.0.1:8000 \
        --clients 25 --tasks 40 --rounds 5

Outputs latency percentiles per endpoint and a pass/fail against thresholds.
"""
import argparse
import asyncio
import json
import statistics
import time
import uuid

import httpx


# One annotation blob of a realistic size (a few dozen polygon points each).
def _annotation_blob(n_shapes: int) -> str:
    shapes = []
    for i in range(n_shapes):
        pts = [{"x": float(x), "y": float(x * 2)} for x in range(24)]
        shapes.append({
            "id": uuid.uuid4().hex, "type": "polygon",
            "labelId": "lbl-1", "points": pts,
        })
    return json.dumps(shapes)


class Metrics:
    """Per-endpoint latency samples, plus error/conflict/lock counters."""

    def __init__(self):
        self.samples: dict[str, list[float]] = {}
        self.errors: dict[str, int] = {}
        self.conflicts = 0
        self.locked = 0

    def record(self, label: str, seconds: float):
        self.samples.setdefault(label, []).append(seconds * 1000.0)

    def error(self, label: str):
        self.errors[label] = self.errors.get(label, 0) + 1

    def report(self) -> dict:
        out = {}
        for label, xs in sorted(self.samples.items()):
            xs_sorted = sorted(xs)
            out[label] = {
                "n": len(xs),
                "p50_ms": round(statistics.median(xs_sorted), 1),
                "p95_ms": round(xs_sorted[int(len(xs_sorted) * 0.95) - 1], 1) if xs_sorted else 0,
                "max_ms": round(max(xs_sorted), 1) if xs_sorted else 0,
            }
        return out


async def _timed(metrics, label, coro):
    start = time.perf_counter()
    resp = await coro
    metrics.record(label, time.perf_counter() - start)
    return resp


async def annotator(client_no, base_url, token, project_id, task_ids, rounds, metrics, barrier):
    client_id = f"load-{client_no}-{uuid.uuid4().hex[:8]}"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=60.0) as http:
        # Shift-start burst: everyone hits the list at the same instant.
        await barrier.wait()
        r = await _timed(metrics, "GET /tasks?list", http.get(
            f"/api/tasks?projectId={project_id}&include_annotations=false"))
        if r.status_code != 200:
            metrics.error("GET /tasks?list")

        # Each client works a disjoint-ish slice so they mostly do not collide,
        # but with deliberate overlap at the edges to exercise the soft lock.
        per = max(1, len(task_ids) // max(1, (len(task_ids) // 4 or 1)))
        my_tasks = task_ids[client_no % len(task_ids)::max(1, len(task_ids) // 8 or 1)] or task_ids[:2]

        for task_id in my_tasks:
            r = await _timed(metrics, "GET /tasks/{id}", http.get(f"/api/tasks/{task_id}"))
            if r.status_code != 200:
                metrics.error("GET /tasks/{id}")
                continue
            updated_at = r.json().get("updated_at")

            r = await _timed(metrics, "POST claim", http.post(
                f"/api/tasks/{task_id}/claim", params={"client_id": client_id}))
            if r.status_code == 200 and r.json().get("status") == "locked":
                metrics.locked += 1

            for _ in range(rounds):
                payload = {
                    "id": task_id, "annotations": _annotation_blob(8),
                    "updated_at": updated_at, "client_id": client_id,
                    "time_spent_delta": 3, "status": "In Progress",
                }
                r = await _timed(metrics, "POST /tasks (save)", http.post("/api/tasks", json=payload))
                if r.status_code == 409:
                    metrics.conflicts += 1
                elif r.status_code != 200:
                    metrics.error("POST /tasks (save)")
                else:
                    updated_at = r.json().get("updated_at")
                await _timed(metrics, "POST heartbeat", http.post(
                    f"/api/tasks/{task_id}/heartbeat", params={"client_id": client_id}))
                await asyncio.sleep(0.05)  # a beat between saves, like a human

            await _timed(metrics, "DELETE claim", http.delete(
                f"/api/tasks/{task_id}/claim", params={"client_id": client_id}))


async def setup(base_url, tasks):
    """Register the shared account and seed a throwaway project with `tasks`."""
    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as http:
        username = f"loadtest-{uuid.uuid4().hex[:8]}"
        r = await http.post("/api/auth/register", json={"username": username, "password": "loadtest-pw-123"})
        r.raise_for_status()
        token = r.json()["access_token"]
        http.cookies.clear()  # force bearer-token auth (CSRF-exempt)
        headers = {"Authorization": f"Bearer {token}"}

        r = await http.post("/api/projects", headers=headers, json={
            "name": "loadtest", "slug": "loadtest", "creator": username})
        r.raise_for_status()
        project_id = r.json()["id"]

        task_ids = []
        for i in range(tasks):
            r = await http.post(f"/api/tasks?projectId={project_id}", headers=headers,
                                 json={"description": f"img-{i}.jpg", "status": "New"})
            r.raise_for_status()
            task_ids.append(r.json()["id"])
        return token, project_id, task_ids


THRESHOLDS_MS = {  # p95 targets; tune to the deployment's acceptable UX
    "GET /tasks?list": 1500,
    "GET /tasks/{id}": 500,
    "POST /tasks (save)": 1000,
    "POST claim": 300,
    "POST heartbeat": 300,
}


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--clients", type=int, default=25)
    ap.add_argument("--tasks", type=int, default=40)
    ap.add_argument("--rounds", type=int, default=5, help="saves per task open")
    args = ap.parse_args()

    print(f"Seeding {args.tasks} tasks on {args.base_url} ...")
    token, project_id, task_ids = await setup(args.base_url, args.tasks)

    metrics = Metrics()
    barrier = asyncio.Barrier(args.clients)
    print(f"Running {args.clients} concurrent annotators, {args.rounds} saves/task ...")
    start = time.perf_counter()
    await asyncio.gather(*[
        annotator(i, args.base_url, token, project_id, task_ids, args.rounds, metrics, barrier)
        for i in range(args.clients)
    ])
    wall = time.perf_counter() - start

    report = metrics.report()
    print(f"\n=== Results ({args.clients} clients, {wall:.1f}s wall) ===")
    print(f"{'endpoint':22s} {'n':>5s} {'p50':>8s} {'p95':>8s} {'max':>8s}  verdict")
    ok = True
    for label, s in report.items():
        threshold = THRESHOLDS_MS.get(label)
        verdict = ""
        if threshold is not None:
            passed = s["p95_ms"] <= threshold
            ok = ok and passed
            verdict = f"{'PASS' if passed else 'FAIL'} (<= {threshold}ms p95)"
        print(f"{label:22s} {s['n']:5d} {s['p50_ms']:8.1f} {s['p95_ms']:8.1f} {s['max_ms']:8.1f}  {verdict}")

    print(f"\nconflicts (409): {metrics.conflicts}   lock-contended: {metrics.locked}")
    if metrics.errors:
        ok = False
        print("errors:", dict(metrics.errors))
    else:
        print("errors: none")
    print(f"\nOVERALL: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
