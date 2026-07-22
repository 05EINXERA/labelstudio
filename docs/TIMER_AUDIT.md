# Timer / Time-Tracking Audit

Audit of the session timer, per-task `time_spent`, and per-user `time_logged`
across the frontend workspace and the FastAPI backend. Written 2026-07-21 on
`feat/sidebar`.

## 1. How it works today

### Data model (`models.py`)

| Column | Meaning | Unit |
|---|---|---|
| `Task.time_spent` | cumulative seconds annotators spent on that task | seconds (Integer) |
| `TeamMember.time_logged` | cumulative seconds a named user has logged overall | seconds (Integer) |

Both are monotonic counters — the server never receives an absolute value, only
deltas that it adds.

### The three counters (`frontend/js/components/timer.js`)

`timerLocalState` holds:

- `sessionSeconds` — display-only "this session" readout (`#sessionTimerDisplay`).
- `totalSeconds` — the user's lifetime total, seeded once at module load from
  `GET /api/team`, then incremented locally (`#totalTimeLogged`).
- `lastSyncedTotalSeconds` — high-water mark; `totalSeconds - lastSynced` is the
  delta POSTed to `/api/team/time`.

`timerState.taskSessionSeconds` (`frontend/js/timer-state.js`) is the fourth
counter — the shared per-task accumulator, drained by whoever saves next.

All four are driven by a single `setInterval(..., 1000)` in `startTimer()`
([timer.js:109-119](frontend/js/components/timer.js#L109-L119)), which
increments each by 1 per tick and calls `syncTimeToServer()` every 30 ticks.

### Lifecycle

- **Start:** manual click on `#timerToggleBtn`, or automatically on the first
  `pointerdown` on the canvas ([timer.js:175-181](frontend/js/components/timer.js#L175-L181)).
- **Pause:** toggle button; clears the interval and force-syncs user time.
- **Reset:** `#timerResetBtn` → confirm → `pauseTimer()` + `sessionSeconds = 0`.
- **Stop:** `#timerStopBtn` → `pauseTimer()` + shows `#sessionModal` with the
  session total. Purely informational; changes no state beyond the pause.
- **Handover between tasks:** `loadGalleryItem()`
  ([init.js:133-137](frontend/js/init.js#L133-L137)) calls `syncTaskTime(prevTask)`
  before switching, which drains `taskSessionSeconds` into the *previous* task.
- **Flush on exit:** `flushPendingSaves()` on `visibilitychange`/`beforeunload`
  ([init.js:50-69](frontend/js/init.js#L50-L69)).

### Drain points for `taskSessionSeconds`

Three separate call sites read-and-zero it, with duplicated POST bodies:

1. `syncTaskTime()` — [timer.js:24-56](frontend/js/components/timer.js#L24-L56)
2. `syncToBackend()` — [workspace.js:64-104](frontend/js/components/workspace.js#L64-L104)
3. the inline "mark Completed" handler — [init.js:682-700](frontend/js/init.js#L682-L700)

### Display

- Workspace header: session + lifetime readouts, `formatTime()` (`HH:MM:SS`,
  [utils.js:28](frontend/js/utils.js#L28)).
- Task table: `time_spent` re-formatted by a hand-rolled copy of `formatTime`
  ([project_details.js:165-169](frontend/project_details.js#L165-L169)), sortable
  by the `time_spent` column.
- Projects list: **no time metric at all** — `/metrics` and `/metrics/batch`
  ([projects.py:24-85](api/routers/projects.py#L24-L85)) return total/completed/
  progress/comments only.

---

## 2. Findings

Severity: **P1** = silent data loss / wrong numbers; **P2** = user-visible
misbehaviour; **P3** = correctness/robustness/consistency.

### F1 (P1) — `setInterval` counts ticks, not time; every counter drifts low

`sessionSeconds++` per 1000 ms tick assumes the tick is exactly 1 s. Browsers
throttle background timers to ≥1 s (often to 1/min in hidden tabs), and the
interval is never rescheduled to compensate. A tab left in the background
under-counts by minutes per hour, and `time_spent` / `time_logged` are
correspondingly wrong. Every downstream number inherits the error.

**Fix:** record `Date.now()` at start/resume and derive elapsed seconds from
wall-clock deltas; keep the interval only to repaint the display.

### F2 (P1) — Lost time on the last task when the tab closes

`flushPendingSaves()` calls `syncTimeToServer()` (user time) but never
`syncTaskTime()`. It only calls `syncToBackend()` **if** `window.backendSyncTimeout`
is pending. If the user annotates, waits for the debounce to fire, then keeps
the timer running for another minute and closes the tab, that minute is
credited to `time_logged` but silently dropped from `Task.time_spent`.
The two counters permanently disagree.

**Fix:** flush the task delta unconditionally in `flushPendingSaves()`, and use
`navigator.sendBeacon` (or `fetch(..., {keepalive:true})`) — a plain async
`fetch` in `beforeunload` is not guaranteed to be sent.

### F3 (P1) — 409 conflict discards the drained time delta

All three drain points zero `taskSessionSeconds` *before* awaiting the response.
On 409 the handler alerts and sets `task.id = null`; on network failure
`.catch(() => {})` swallows it ([timer.js:54](frontend/js/components/timer.js#L54)).
Either way the seconds are gone — they were removed from the accumulator and
never landed in the DB.

**Fix:** capture the delta, and on any non-2xx/rejected response add it back
(`timerState.taskSessionSeconds += timeDelta`) so the next sync retries it.

### F4 (P1) — Race: three drain points can double-drain or split a delta

`syncTaskTime`, `syncToBackend` and the Completed handler each do a
non-atomic read-then-zero and can be in flight concurrently (the 30 s tick sync,
the debounced autosave, and a gallery switch can overlap). Interleaving splits
one interval's seconds across two POSTs, or — worse — `syncToBackend()` fires
during a gallery switch and credits the delta to whichever task
`state.galleryIndex` currently points at, i.e. the **wrong task**.

**Fix:** single `drainTaskTime(task)` helper in `timer.js`; make `syncToBackend`
and the Completed handler call it instead of touching `timerState` directly.
Bind the task id at drain time, not at response time.

### F5 (P2) — Reset clears the display but not the accrued task/user time

`timerResetBtn` zeroes `sessionSeconds` only. `taskSessionSeconds`,
`totalSeconds` and `lastSyncedTotalSeconds` are untouched, and any seconds
already synced are unrecoverable. The confirm text — *"This will clear your
current session time"* — promises something the code does not do; users will
reasonably read it as "discard this session's logged time".

**Fix:** decide the intended semantics and make them match. Recommend:
reset = discard the *unsynced* portion (`taskSessionSeconds = 0`,
`totalSeconds = lastSyncedTotalSeconds`, `sessionSeconds = 0`) and reword the
confirm to say already-saved time is kept.

### F6 (P2) — Stop is indistinguishable from Pause

`timerStopBtn` calls `pauseTimer()` and opens a summary modal. There is no
session-end semantic: no task-time flush, no `sessionSeconds` reset, and
pressing play afterwards resumes the same session. Three controls, two
behaviours.

**Fix:** make Stop = flush everything (task + user) then reset `sessionSeconds`
to 0 after the modal is acknowledged — a real end-of-session — or drop the
button and keep pause/reset.

### F7 (P2) — `totalSeconds` seeding is fragile and identity is `localStorage`-based

The seed IIFE ([timer.js:59-74](frontend/js/components/timer.js#L59-L74)) races
the timer: if the user clicks the canvas before `GET /api/team` resolves, the
response overwrites `totalSeconds`, discarding the seconds already ticked, and
resets `lastSyncedTotalSeconds` — so those seconds never sync. On fetch failure
the display starts at 00:00:00 and the delta arithmetic still works, but the
readout lies for the whole session.

Separately, timer identity is `localStorage['dataset_username']`, not the
authenticated user from the cookie. It is user-editable, and `'Unknown'`
silently disables all user-time sync (`syncTimeToServer` early-returns).
`/api/team/time` also no-ops silently when the member row doesn't exist
([team.py:36-40](api/routers/team.py#L36-L40)) — time is accepted and discarded.

**Fix:** seed before allowing start (or add the pending seconds to the fetched
base rather than replacing); derive the member from `get_current_user` server-side
and ignore the client-supplied `name`; return 404 instead of `{"status":"ok"}`
when the member is missing.

### F8 (P2) — Timer keeps running with no task, and across task switches

Nothing stops the interval when the workspace has no loaded task
(`galleryIndex < 0`). `taskSessionSeconds` accrues and is credited to whatever
task loads next. Likewise the timer is never auto-paused on idle, so leaving the
tab open on a coffee break inflates both counters.

**Fix:** don't accrue `taskSessionSeconds` when there is no current task; add an
idle auto-pause (e.g. no pointer/key input for N minutes).

### F9 (P3) — `time_spent_delta` is unvalidated and unsigned

`schemas.py:26` declares `time_spent_delta: Optional[int] = 0` with no bound.
A negative value silently decrements `Task.time_spent`
([tasks.py:65-66](api/routers/tasks.py#L65-L66)); a huge value poisons the metric.
`TeamTime.time_logged` ([schemas.py:41-43](schemas.py#L41-L43)) has the same hole.
`/api/tasks` and `/api/team` are also *unauthenticated* (CLAUDE.md rule 1), so
these are anonymously writable.

**Fix:** `ge=0` plus a sane upper bound (e.g. `le=86400`) on both fields; add
`dependencies=[Depends(get_current_user)]` to the tasks router.

### F10 (P3) — Naive `utcnow()` timestamps corrupt the conflict check

`tasks.py` uses `datetime.datetime.utcnow()` (deprecated, naive) at lines 69, 82
and 119, violating CLAUDE.md rule 7. The 409 check
([tasks.py:52-58](api/routers/tasks.py#L52-L58)) strips the client tzinfo to
compare, and `except ValueError: pass` swallows malformed timestamps — silently
skipping conflict detection. Since a failed conflict check is what triggers F3,
these interact.

**Fix:** `datetime.now(timezone.utc)` throughout, store tz-aware, and reject
(422) rather than ignore an unparseable `updated_at`.

### F11 (P3) — `formatTime` duplicated, and no unit past 99 hours

`project_details.js:165-169` re-implements `frontend/js/utils.js:28` verbatim
(and adds a `'-'` for zero, which the workspace readout does not do). `HH:MM:SS`
also has no overflow handling — a task past 100 h renders `100:00:00` and breaks
the column alignment.

**Fix:** import `formatTime` from `utils.js` in `project_details.js`; add an
optional zero-placeholder argument.

### F12 (P3) — No time metrics at project level

Neither `/metrics` nor `/metrics/batch` aggregates `time_spent`, so the projects
list shows progress but never effort. This is the most obvious missing feature
rather than a bug, and it's cheap: both endpoints already scan the task rows.

**Fix:** add `SUM(time_spent)` as `total_time` and `avg_time_per_task` to both
endpoints; render in the project card/table.

### F13 (P3) — Metrics endpoints violate house rules

`get_project_metrics` is a GET; CLAUDE.md rule 4 flags it as writing to the DB.
`projects.py:67` has `import json` mid-function and `:87` `import schemas`
mid-file (rule 2), and `:77-78` is a bare `except Exception: pass` (rule 3).
Worth fixing in the same pass since F12 touches these functions.

---

## 2a. Status

All five phases are implemented on `feat/sidebar` (2026-07-21).

| Finding | Status |
|---|---|
| F1 tick-counting drift | **Fixed** — wall-clock accrual in `timer.js` |
| F2 lost tail on unload | **Fixed** — unconditional flush + `sendBeacon` |
| F3 delta lost on failure | **Fixed** — delta returned to accumulator on failure |
| F4 racing drain points | **Fixed** — single `drainTaskTime()` |
| F5 reset semantics | **Fixed** — discards unsynced time; confirm text matches |
| F6 stop vs pause | **Fixed** — flushes both counters, then starts a fresh session |
| F7 seed race / identity | **Fixed** — additive seed; server-side identity |
| F8 accrual with no task | **Fixed** — task-resolver gate + 5 min idle auto-pause |
| F9 unvalidated delta | **Fixed** — `ge=0, le=86400`; tasks router authed |
| F10 naive `utcnow()` | **Fixed** — tz-aware; 422 on bad `updated_at` |
| F11 duplicated `formatTime` | **Fixed** — shared helper; overflow-safe past 99h |
| F12 no project time metrics | **Fixed** — `total_time` / `avg_time_per_task` + dashboard column |
| F13 metrics house rules | **Fixed** — GET no longer writes; imports hoisted |

Verified end to end: 660 s of throttled background time accrues fully (old code
recorded 70 s); deltas accumulate 30+45=75 through the API; negative/oversized
deltas now 422 (previously `-1000` drove `time_spent` to `-925`); a forged
`name` in `/api/team/time` credits the authenticated user and creates no
impersonated row; reset keeps synced time while discarding the unsynced
remainder; idle rollback clamps at the synced high-water mark without going
negative; project metrics report 5400 s total / 2700 s average, and
`GET /metrics` no longer mutates project status while task updates still
promote a project to `Completed`.

**Note on status derivation (F13):** removing the write from `GET /metrics`
moved it to the task-update path. That aggregate must run after `db.flush()` —
without it the project never reaches `Completed` on the update that completes
its last task. This was caught in testing and fixed.

## 3. Implementation plan

Ordered so each phase is independently shippable and testable. Phases 1–2 are
the data-integrity work; 3–5 are behaviour and polish.

### Phase 1 — Make the clock and the drain correct (F1, F3, F4)

1. **`timer.js`: wall-clock accounting.** Replace tick-counting with a
   `runStartedAt` timestamp + `accumulatedMs`. On each repaint tick derive
   `sessionSeconds` from `Date.now()`. Advance `totalSeconds` and
   `taskSessionSeconds` by the *measured* delta since the last tick.
2. **`timer.js`: single `drainTaskTime(task)` export.** Captures the delta,
   POSTs, and on failure/409 returns the delta to `timerState.taskSessionSeconds`.
   Bind `task.id` at call time.
3. **Rewire callers.** `syncToBackend()` (workspace.js) and the Completed handler
   (init.js) call `drainTaskTime`; delete their inline read-and-zero blocks and
   duplicated POST bodies.
4. **`syncTimeToServer` failure path.** Only advance `lastSyncedTotalSeconds`
   after a 2xx response, not optimistically before the request settles.
5. Test: unit-test the accounting helper (fake timers, simulated throttling);
   assert that a rejected sync leaves the accumulator intact.

### Phase 2 — Don't lose the tail (F2, F7-seed)

6. `flushPendingSaves()` flushes the task delta unconditionally.
7. Switch unload-path syncs to `navigator.sendBeacon`, falling back to
   `fetch(..., {keepalive: true})`.
8. Fix the seeding race: gate `startTimer()` on the seed promise, or add pending
   seconds onto the fetched base instead of overwriting.
9. Test: manual — annotate, idle 60 s, close tab, verify `time_spent` and
   `time_logged` moved by the same amount.

### Phase 3 — Backend hardening (F9, F10, F13)

10. `schemas.py`: `time_spent_delta: int = Field(0, ge=0, le=86400)`; same bounds
    on `TeamTime.time_logged`.
11. `tasks.py`: `datetime.now(timezone.utc)` everywhere; 422 on unparseable
    `updated_at` instead of `except ValueError: pass`.
12. Add `dependencies=[Depends(get_current_user)]` to the tasks and team routers
    (CLAUDE.md rule 1) and derive the team member from the authenticated user in
    `/api/team/time`; 404 when missing.
13. `projects.py`: hoist `import json` / `import schemas` to the top, replace the
    bare `except: pass`, and remove the GET-writes-DB path.
14. Tests in `tests/`: negative delta → 422; oversized delta → 422; unauthenticated
    POST → 401; delta accumulates correctly across two calls.

### Phase 4 — Coherent pause/reset/stop semantics (F5, F6, F8)

15. Define and document the three actions in the UI:
    - **Pause** — stop accruing, keep everything, flush user time.
    - **Reset** — discard *unsynced* session time on all counters; reword the
      confirm dialog to state that already-saved time is kept.
    - **Stop** — flush task + user time, show the summary modal, then zero
      `sessionSeconds` for a fresh session.
16. Suppress `taskSessionSeconds` accrual when `state.galleryIndex < 0`.
17. Idle auto-pause after N minutes without input (default 5, in one place so it
    can be tuned).
18. Test: manual matrix over the three buttons × (task loaded / no task).

### Phase 5 — Reporting and display (F11, F12)

19. `project_details.js` imports `formatTime` from `utils.js`; delete the copy.
    Add an overflow-safe format for ≥100 h.
20. `projects.py`: add `total_time` and `avg_time_per_task` to `/metrics` and
    `/metrics/batch` via `SUM(time_spent)`; declare `response_model` schemas
    (CLAUDE.md rule 6).
21. Surface total time on the project card / list and the project detail header.
22. Test: metrics endpoint returns the correct sum for a project with mixed
    task times.

### Suggested branches

| Branch | Phases | Findings |
|---|---|---|
| `fix/timer-accounting` | 1–2 | F1, F2, F3, F4, F7 (seed) |
| `fix/time-api-hardening` | 3 | F9, F10, F13, F7 (identity) |
| `feat/timer-controls` | 4 | F5, F6, F8 |
| `feat/project-time-metrics` | 5 | F11, F12 |
