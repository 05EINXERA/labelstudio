# CLAUDE.md — Project Instructions

## What this project is

A browser-based image annotation workspace ("mini Label Studio"). Users create
projects, upload images as tasks, and draw bounding boxes / polygons on an
HTML5 canvas. AI assistance comes from local models: YOLOv8 / YOLO-World
(auto-detect), Meta SAM (magic-wand segmentation), and CLIP (auto-tagging).

- **Backend:** FastAPI (Python) + SQLAlchemy. Entry point: `main.py`.
- **Database:** SQLite (WAL) for development, **PostgreSQL** for the multi-user
  LAN deployment. The engine is chosen from `config.DATABASE_URL`
  (`config.IS_SQLITE` switches the dialect-specific settings). See `database.py`.
- **Frontend:** Vanilla JS + HTML5 Canvas, served as static files from `frontend/`. No build step, no framework.
- **ML:** `detector.py` loads and runs the models. Inference runs through an in-process background job queue (`api/routers/detect.py`).
- **Deploy target:** one PC on an office LAN, one uvicorn process, serving ~20–25
  annotators who **share a single login** (classes/images uploaded once are
  visible to all; per-person task assignment is advisory). Postgres runs on the
  same box. Plain HTTP on the trusted LAN (TLS deferred).

**Deployment/hardening state lives in `.devnotes/deployment-hardening/`** — read
it before touching auth, config, the DB layer, task save/conflict logic, or the
soft-lock: `01_AUDIT.md` (current audit + wins/lags), `tasks.md` (phased task
list; Phases 0–4 done — see `05_LOAD_TEST.md` for load-test results),
`04_ANNOTATION_SAVE_LOSS.md` (the save-loss bug and its fix — the model that
makes concurrent editing safe), `06_RESILIENCE_PLAN.md` (crash/power-loss/
backup robustness for the single-PC deployment), `08_BACKUP_TRUNCATION.md`
(why hourly dumps silently truncated — the deploy box is a laptop and Task
Scheduler stops tasks on battery — plus what makes the dumps 3.8 GB).

**Teams, roles and project access live in `.devnotes/teams/`** — read it before
touching authorization, `api/permissions.py`, grants, task assignment or the
review flow: `01_DESIGN.md` (the model — §2 the two role axes, §6 the resolver),
`02_SCHEMA.md` (columns, cascades, migrations), `03_API.md` (endpoints and the
minimum role for every call site), `04_UI_UX.md` (what each role sees),
`06_EDGE_CASES.md` (30 numbered cases), `PLAN.md` §8 (deviations actually made).
Phases 1–5 are complete; `07_PHASING.md` lists the F1–F9 follow-ups that are
deliberately **not** built.

Read `docs/ARCHITECTURE.md` before moving code between modules,
`docs/CONVENTIONS.md` before writing new code, and `docs/GOTCHAS.md` before
copying any existing pattern — several existing patterns are known mistakes.

## Rules for AI assistants and developers

These are prescriptive. Where existing code disagrees with a rule, the rule
wins; fix the old code opportunistically when you touch it, and never copy the
old pattern into new code.

### How to work here (read this first)

The rules below say *what* this codebase requires. This section says how to
work so that you actually satisfy them instead of believing you have. Every
item is here because it has already gone wrong in this repo, usually expensively.

**A. Verify instead of recalling. Your memory of this codebase is a guess.**
Before you state how something behaves, open the file that decides it. "JS
changes need a hard reload" sat in rule 13 for months and was false — the
`no-cache` middleware in `main.py` had made an ordinary reload sufficient, and
nobody re-read the header. A claim about this system is worth exactly as much
as the file, test run or command output you can point at. If you did not look,
say you did not look.

**B. Report what happened, not what you intended.** When you say a test was
added, a suite passed, or a file was edited, that must describe a result you
observed — not the action you attempted. A string-replace that silently matches
nothing, a stale log read as if it were fresh, a suite whose exit code you never
checked: each produces a confident, false report, and a false green is far more
expensive than a red. If a step was skipped, blocked or unverified, say so in
the same breath as the thing that worked.

**C. Measure before you optimise, and look at the real data.** The server stall
was not guessed; it was found by timing the blob parse at ~185 ms of GIL-held
CPU (`.devnotes/server-issue-diagnosis/`). The backup truncation turned out to
be a laptop on battery and Task Scheduler, not anything in the dump code
(`08_BACKUP_TRUNCATION.md`). Both were invisible from the source and obvious
from the data. Reach for a measurement, a row count, an actual payload.

**D. Prefer the boring solution; earn every abstraction.** This codebase is
deliberately plain — vanilla JS, no build step, one router per resource, pure
modules you can run under `node` in a second. That is a feature. Do not add a
framework, a layer, a config switch, a cache or a generalisation on the first
occurrence of a problem. Write the direct version, make it correct, and let the
*second* real case tell you what the abstraction should be. A clever structure
that one person understands is a liability in a shared repo.

**E. Solve the problem in front of you, at its actual size.** Fix the bug that
was reported; do not quietly rewrite the subsystem around it. If you discover
a second, larger problem, say so and let the reader decide — do not fold it
into the change. A diff that does one thing can be reviewed; one that does four
cannot, and this is a repo where unreviewable changes have destroyed annotator
work.

**F. Assume your change is wrong until something external says otherwise.**
Run the suite, run the node spec, exercise the real path. The pure modules
(`untangle.js`, `merge.js`, `objects-filter.js`, `formats/`) exist precisely so
that this is cheap — a spec runs in well under a second. Favour a failing test
you can watch go green over an argument about why the code must be correct.

**G. Treat silent correctness as the enemy.** The worst failures here have all
been quiet: a draft that stopped saving past ~1,000 polygons because
localStorage filled, a timestamp-only conflict check that dropped a second
annotator's work, a save that removed shapes nobody deleted. When you write a
path that can fail, make the failure visible (rule 3) — never leave the system
confidently doing the wrong thing.

**H. Say when you are unsure, and stop at the real boundary.** "I think this is
right but I did not run it" is useful; false confidence is not. For anything
touching production data, the `dev` branch, backups, migrations or a destructive
git operation, state the risk and ask rather than proceeding on assumption.

### Backend

1. **All `/api/*` routes require auth** via `dependencies=[Depends(get_current_user)]` on the router — except `/api/auth/*`. This now holds across every router (the old `tasks.py`/`data.py`/`label_studio.py` gaps are closed). Any new router must include the auth dependency.
1a. **State-changing routers also require CSRF** via `Depends(require_csrf)` (see `api/auth.py`): a double-submit token check exempting pure `Authorization: Bearer` clients. Routers that mutate (e.g. `tasks.py`, `labels.py`) carry it; new mutating routers must too.
1b. **Authorization goes through `api/permissions.py`, never `owner_id` directly.** Use `require_project(pid, user, db, minimum=ProjectRole.X)` / `require_task(...)` with the minimum role the endpoint actually needs, and `accessible_project_ids(user, db)` for list endpoints. `get_owned_project` / `_get_owned_task` / `_owned_project_ids` survive only as deprecated aliases and are deleted in Phase 5 F5 — do not call them from new code. The per-call-site minimums are tabulated in `.devnotes/teams/03_API.md` §4.1; two that look wrong are deliberate (**exports at `reviewer`** — a read, but not one every annotator should one-click the whole dataset with; **imports at `manager`** — a replace-mode import wipes labels for everyone). Contract: **404** when the caller has no role at all (identical to a nonexistent id, so ids cannot be enumerated), **403** when they have a role but not a high enough one, with a message naming the role required.
1c. **Permission checks run before conflict detection**, always. A 403 must never surface as a 409 — a user who lacks permission needs an actionable message, not "someone else edited this". See `api/routers/tasks.py`'s update branch for the required ordering.
1d. **Every write to `Task.assigned_team_id` / `assignee_user_id` records history.** Snapshot the old values with `api/assignment_history.snapshot`, make the change, then `record(...)` before the one `commit_with_retry` — the event and the change land in the same transaction, and `record` never commits. The log (`task_assignment_events`) is append-only and is the history only; the two task columns stay the authority on current state. A no-op write adds no row. System clears (member removed/left, grant revoked, team deleted) are recorded too, or the history disagrees with the table. `tests/test_assignment_history_writers.py` fails if a file outside its allow-list starts writing the columns. The legacy free-text `tasks.assignee` is *not* part of this. See `.devnotes/features/task-assignment-history/`.

2. **Imports go at the top of the file.** Existing code has `import json` inside functions and `import schemas` mid-file — do not copy that.
3. **No bare `except:` and no silent `pass`.** Catch the specific exception, and either handle it meaningfully or log it. See CONVENTIONS.md § Errors.
4. **GET endpoints must not write to the database.**
5. **Use correct HTTP methods going forward:** `POST` create, `PATCH` update, `DELETE` delete. The existing `POST /api/projects/update` style is legacy; new endpoints must not follow it.
6. **Declare `response_model` with a Pydantic schema** for new endpoints instead of returning hand-built dicts (e.g. `TaskDetail` on `GET /api/tasks/{id}`).
7. **Datetimes:** always timezone-aware UTC — `datetime.now(timezone.utc)`, never `datetime.utcnow()` (deprecated, returns naive datetimes).
8. **Schema changes go through Alembic** (`alembic revision --autogenerate`), not by relying on `Base.metadata.create_all` (which only creates missing tables, never alters existing ones). The migration chain must build the schema on an **empty** database (Postgres deploys start empty) — do not write migrations that only `ALTER` pre-existing tables.
9. **Never touch `JOBS`/`_models`/`_TASK_LOCKS` from a second process/worker.** These are in-process dicts (the AI job queue, loaded ML models, and the soft task lock in `tasks.py`), as are the attendance buffer and the work-session tracker (`api/work_sessions.py` `_LIVE`/`_CLOSED`; under two workers it silently splits stretches). The app must run as exactly one uvicorn worker until this state is moved out of process. **Do not add `--workers N`.**
10. **Concurrent-write commits use `commit_with_retry(db)`** (from `database.py`), not raw `db.commit()` — it backs off on lock/deadlock/serialization contention. All router commits route through it.
11. **Task save conflict model:** writes carry a per-tab `client_id`; `tasks.last_client_id` records the last writer. A conflict (409) is only raised when a *different* client wrote since the caller read — a client never conflicts with itself. Do not reintroduce a timestamp-only check, and never disable client-side saving on a 409. See `.devnotes/deployment-hardening/04_ANNOTATION_SAVE_LOSS.md`.
11a. **The task-status vocabulary lives in `schemas.py`, and approval is a *group*.** `APPROVED_STATUSES` ("Approved", "Verified", "Checked", "Passed") are synonyms differing only in which **export batch** a sign-off belongs to — the team approves each week under a fresh name so an export can select just the new work instead of re-shipping everything ever approved. `REVIEW_STATUSES` (reviewer-gated) and `TERMINAL_STATUSES` (demote-on-edit) are *derived* from it, as are the interop mapping (`formats/common.py`), the review verbs and the completion statistics. **Adding a batch status is one line in `APPROVED_STATUSES` plus its verb in `ReviewActionLiteral`** (a Pydantic `Literal` cannot be built from a variable; an import-time assert catches the drift) and one line in `frontend/js/task-status.js`. Never test a status by name where the group is meant — `status == "Approved"` silently excludes three batches. Completion means *signed off*: `Completed` is the annotator's submission and is counted as `awaiting_review`, not as done.
11b. **Annotations are rows, never a blob.** They live in the `annotations` table, one row per shape, read through `formats.common.annotation_dicts(task)` and written through `formats.annotation_rows.sync_task_annotations_for_project(...)` — never by touching `Task.annotations`, which is the dead legacy column kept as a rollback path. The wire format is unchanged (clients still send and receive a JSON array), so this is a storage rule, not an API one. **Never count or filter annotations in Python where SQL can do it**: counting a blob cost ~185 ms of GIL-held CPU per parse and is what stalled the server (`.devnotes/server-issue-diagnosis/`). The save diffs against the stored rows read once as plain tuples (`load_stored_rows`) — **not** `task.annotation_rows`, which builds an ORM object per shape and is lazy (GOTCHAS #19) — and compares `points` by value, not by re-serialising (GOTCHAS #20). Two schema choices are load-bearing and must not be "tidied": the primary key is composite `(id, task_id)` because the same shape id legitimately appears on several tasks, and `type` is nullable because thousands of real annotations have none. See `.devnotes/performance-fixes/`.
11c. **A save may not remove shapes the user did not delete.** Every save carries `deleted_ids` (the user's deliberate removals since the last accepted save), and the server refuses (422) a loss those ids do not explain once it is ≥ `WIPE_GUARD_MIN_LOST` shapes and > `WIPE_GUARD_RATIO` of the task; an empty payload is refused on any unexplained loss. **Any new client code path that removes annotations must call `noteUserRemoved(before, after)`** (`state.js`), or its large removals will be refused; hydration and draft restore must not call it. Never re-add a yes/no "allow delete" flag or infer intent from an empty canvas. `frontend/js/wipe-guard.js` mirrors the rule (rule 18b applies). See `.devnotes/bulk-loss-guard/01_DESIGN.md`.
12. **Configuration comes from `config.py`**, which loads `.env` for every entry point (uvicorn, alembic, scripts) — never read `os.environ` for deployment settings elsewhere, and never rely on the launcher script to have loaded `.env`.

### Frontend

13. **All frontend code lives in ES modules under `frontend/js/`**, imported from the page scripts. The old `frontend/app.js` monolith is gone — the canvas page is fully decomposed (`init.js` is the entry point, with `components/`, `canvas/`, `pages/` beneath it); do not recreate a catch-all file. Module imports are version-pinned (`./foo.js?v=1`); the pin is the only invalidation mechanism (content-hashing is a deferred item — see tasks.md D4). **Bump the pin at *every* import site of a changed module, plus the entry `<script>` tag in the page** — a partial bump ships clients a mix of old and new modules. Annotators then need an ordinary reload, **not** a hard reload: `main.py`'s middleware serves HTML/JS/CSS as `Cache-Control: no-cache`, so the browser revalidates the page on every load, gets the new pins, and fetches the changed modules as new URLs. What a reload cannot do is reach a tab nobody reloads — an open canvas keeps running the old modules until someone reloads it, so a behavioural change does not take effect across the floor until then.
14. **Auth state lives in the httpOnly cookie.** `localStorage['logged_in']` is only a UI hint for redirects — never treat it as security.
15. **Modals:** toggle with `classList.add/remove('is-active')`, never `style.display` (CSS transitions depend on the class — see `.agents/AGENTS.md`).
16. All backend calls from authenticated pages go through the `apiFetch` wrapper (handles 401 → redirect), not raw `fetch`.
17. **Per-task annotation loading.** The gallery list is fetched annotation-free (`include_annotations=false`); annotations hydrate per task on open via `GET /api/tasks/{id}`. Do not go back to loading every task's annotations up front.
18. **Unsaved work is protected by a per-task localStorage draft** (`draftKey(taskId)` in `state.js`), restored on task open and cleared only on server-confirmed save. There is deliberately no cross-tab `storage` listener reloading annotations, and no single global draft slot. See 04_ANNOTATION_SAVE_LOSS.md. localStorage's ~5 MB quota silently disabled the draft for tasks past ~1,000 polygons, so a draft that does not fit **overflows to IndexedDB** (`draft-overflow.js`) — localStorage stays primary because it is synchronous (on disk before `pagehide` returns); a draft that fits nowhere is reported in the save indicator, never swallowed. The draft write is debounced (400 ms) and flushed by `flushDraft()` on `pagehide`/`visibilitychange`.
18e. **Automatic saves are paced, and only automatic saves.** `autosave-pacing.js` stretches the debounced autosave to `clamp(2.5 × smoothed save round trip, 1 s, 10 s)` so a slow server gets *fewer* full-set uploads instead of more (the coalescer alone starts the next save the instant one settles). The 10 s ceiling is the most unsaved work a tab may hold. Beacons, the Save button, explicit status changes and the unload/switch flushes never wait. The indicator must not read "Saved" while an autosave is scheduled or in flight (`restingStatus()`).
18a. **A permission error never destroys unsaved work.** No code path may clear a draft or drop an offline-queue item on a 403. The queue marks such an entry `forbidden`, reports it once and stops retrying (a 403 is not a retryable network error, and the server is answering fine) — but *keeps* the payload. See `.devnotes/teams/06_EDGE_CASES.md` E-08, E-24, E-27.
18b. **Client-side role checks are for rendering only.** `frontend/js/permissions.js` mirrors `api/permissions.py`'s ranking because there is no build step to share one definition; both files carry a comment pointing at the other. Never remove a server check because the client hides the control — a stale bundle is a cosmetic bug (E-17), a missing server check is a vulnerability. `tests/js/permissions_spec.mjs` guards the two copies against drift.
18c. **Identity comes from `GET /api/auth/me`** (via `frontend/js/session.js`), never from `localStorage['dataset_username']` — that is free text the user typed into a prompt and is frequently wrong. It survives only as a display fallback for cached bundles mid-rollout and is removed in Phase 5 F3.
18d. **Role-gated routes are checked in the router, not just hidden in the nav.** A hidden tab does nothing about a typed, bookmarked or shared URL, and a role can be revoked after a link was saved. Both hash routers (`pages/project/router.js`, `pages/team/router.js`) resolve a disallowed route to the default one and normalise the address bar to match.

### Repo hygiene

19. **Never commit:** model weights (`*.pt`, `*.onnx`), `workspace.db*`, `uploads/`, `.jwt_secret`, `.env`, `backups/`, or any credentials. `.jwt_secret` was committed historically and must be treated as compromised (see GOTCHAS.md #1).
20. **One-off/debug scripts go in `scripts/`**, not the repo root, and are never named `test_*.py` (that prefix is reserved for pytest). Root files `test_sam_mask.py`, `test_upload.py`, `check_endpoints.py`, `debug_hang.py` are legacy manual scripts, not tests.
21. **Real tests live in `tests/`** and run with pytest. New backend endpoints and bug fixes should come with a test.
22. New dependencies must be added to `requirements.txt` with a version constraint in the same commit that introduces the import.

### Workflow

- **`dev` (production) and `dev-stage` differ by one thing: the temporary network-telemetry feature** (`api/routers/telemetry.py`, `api/telemetry_timing.py`, `frontend/js/telemetry/`, `scripts/telemetry_report.py`, `tests/*telemetry*`, `TELEMETRY_*` in `config.py`/`.env.example`, the `boot.js` tag in each `frontend/*.html`, a few lines in `main.py`/`schemas.py`). It must never reach `dev`. So: **never merge `dev-stage` into `dev`**. Branch new work from `dev`, then merge that branch into `dev` (in the production worktree, where `dev` is checked out) and into `dev-stage` separately. Expect a trivial conflict in the `frontend/*.html` script tags on the `dev-stage` merge: keep the telemetry `<script>` and take the new `?v=` pin. Before merging into `dev`, `git diff dev <branch> --stat` must show no telemetry file. Removal is `chore/remove-network-telemetry`; see `.devnotes/frontend-telemetry/06_ROLLBACK_AND_CLEANUP.md`.
- **Branch from `dev`**, never from `main`: `feat/<slug>`, `fix/<slug>`, `docs/<slug>`. `main` is abandoned at the initial commit (2026-07-19) and is hundreds of commits behind; branching from it produces a diff against the wrong world. `dev` is the production branch. Parts of `docs/DEVELOPMENT_GUIDE.md` still say `main` and are wrong on that point — this rule wins.
- Commits: imperative summary line ≤ 72 chars, conventional prefix (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, `test:`).
- Before pushing: run the app locally (`venv\Scripts\uvicorn.exe main:app --port 8001`, or `scripts/run-dev.ps1` which loads `.env`) and exercise the feature; run `pytest tests/` if tests exist for the area (see
  *Running the tests* below — bare `pytest` does not work here).
- Full workflow: `docs/DEVELOPMENT_GUIDE.md` (note: its branch instructions still say `main` and are stale — see the branching rule above).

### Keeping this file true

This file is the first thing every agent reads and the only context many of them
get. A wrong line here is worse than a missing one: it is believed and acted on.
It has drifted before — it told people to branch from an abandoned `main`, and
claimed JS changes needed a hard reload long after that stopped being true.

**Update CLAUDE.md in the same commit as the change** when your work does any of:

- changes a rule above, or makes one false (rule 13's reload claim is the cautionary example);
- adds or removes a router, a top-level module, or a directory named in the key file map;
- changes the branch/merge/deploy workflow, or anything about `dev` vs `dev-stage`;
- establishes a new invariant someone could unknowingly break — especially one
  mirrored in two places (`permissions.py`/`permissions.js`,
  `schemas.py`/`task-status.js`, `config.py`/`wipe-guard.js`);
- removes a deprecated alias this file still says exists, or completes a phase
  it describes as pending (`get_owned_project` F5, `dataset_username` F3, the
  telemetry removal);
- changes how the tests are run, or materially changes the pre-existing-failure count.

**Do not** add a line for an ordinary bug fix, a new endpoint that follows the
existing rules, or anything already obvious from the code. This file is a map of
the things you cannot infer by reading — decisions, traps and deliberate
weirdness. Length is a cost: every line competes for attention with the rules
that matter.

**When you find a line here that is wrong, fix it then** — do not route around
it silently. A stale rule that everyone has learned to ignore is the failure
mode this section exists to prevent. Keep `AGENTS.md` pointing here rather than
restating anything; it is a pointer precisely because the duplicate copy drifted
for months.

**Mid-session staleness is handled for you.** `.claude/hooks/claude_md_freshness.py`
re-injects a diff when this file changes on disk after a session started (the
system-prompt copy is a snapshot and is never refreshed). If you see a
`[project-instructions-changed]` block, that diff wins over your system prompt.

### Running the tests

**Gather everything in one pass.** The suite takes ~5m40s (measured 2026-10-06,
1,607 tests), so do not run it, read the failures, and then run it again to
check them against a baseline. Set up the comparison *before* the first run and
get both results together:

```bash
# Baseline in a worktree at the pre-change commit, current tree in place.
git worktree add /tmp/base <pre-change-sha>
python -m pytest tests/ -q --basetemp=./.pt-cur  > /tmp/cur.txt  2>&1 &
(cd /tmp/base && python -m pytest tests/ -q --basetemp=./.pt-base > /tmp/base.txt 2>&1) &
wait
diff <(grep -aE '^FAILED' /tmp/base.txt | sed 's/ - .*//' | sort) \
     <(grep -aE '^FAILED' /tmp/cur.txt  | sed 's/ - .*//' | sort)
```

A worktree beats `git stash` for the baseline: it needs no clean tree, cannot
lose uncommitted work, and works when the change is already committed (stash
would only reach the previous commit, not the pre-feature state).

Points that will otherwise cost a re-run:

- **Run `pytest tests/`, never bare `pytest`.** Root-level `test_sam_mask.py` and
  `test_upload.py` are legacy manual scripts (rule 20) that need a live server;
  they break *collection*, so a bare run reports 0 tests instead of the suite.
- **Pass `--basetemp=./.pt-<name>` when running suites in parallel or after an
  interrupted run.** The default under `AppData\Local\Temp` intermittently hits
  `PermissionError: [WinError 5]` on the `pytest-current` symlink — a collection
  error that looks like a real failure and is not. An explicit basetemp also
  keeps two concurrent runs from sharing one temp root. Delete the directory
  afterwards; it is not gitignored.
- **The suite has 35 pre-existing failures** (`35 failed, 1568 passed, 4 skipped`,
  measured 2026-10-06 on `dev-stage`). They are not only the format fixtures:
  masks 11, import/export formats 6, YOLO 5, labels_bulk 3, image_outputs 3,
  exports 2, and one each in task_save_conflicts, logging_service, imports,
  import_yolo_and_rejections and deployment_hardening. Re-measure rather than
  trusting this count if it looks stale — it is a snapshot, not an invariant.
  Diff against a baseline before attributing any of them
  to your change, and be aware at least one is order-dependent and flaky
  (`test_class_set_file_is_redirected_to_classes_import`) — confirm a suspect by
  re-running it alone rather than assuming a diff of one is meaningful.
- **Use `grep -a` when scanning saved pytest output.** A full-suite log can trip
  the binary-file heuristic, so plain `grep`/`diff` answers "Binary file
  matches" and silently skips the content instead of comparing it.
- **Frontend specs are separate:** `node tests/js/<name>_spec.mjs` prints
  `N passed, M failed`. Each is also wrapped by a pytest file that skips when
  node is absent, so `tests/` covers them — but running node directly is instant
  and is the fast loop while iterating on a pure module.
- **Restart the local server before a manual check.** A uvicorn left running
  from an earlier session serves the module pins it started with, so a new
  module 404s and the feature looks missing when it is not.

## Key file map

| Path | What it is |
|---|---|
| `main.py` | FastAPI app assembly: middleware, router mounting, static files |
| `config.py` | Central config: loads `.env`, resolves `DATABASE_URL`/`APP_HOST`/CORS/JWT/etc., fail-fast `validate_config()` in production |
| `.env` | Deployment config (gitignored). Loaded by `config.py`, not just the launcher |
| `database.py` | Engine/session for SQLite **or** Postgres (via `IS_SQLITE`), pool config, `get_db`, `commit_with_retry` |
| `models.py` | SQLAlchemy ORM models (database tables) |
| `schemas.py` | Pydantic request/response schemas (`TaskDetail`, etc.) |
| `api/auth.py` | JWT creation/validation, password hashing, `get_current_user`, `require_csrf`, session/CSRF cookies |
| `api/permissions.py` | **The authorization resolver.** `ProjectRole`/`TeamRole`, `effective_project_role`, `require_project`, `require_task`, `require_team`, `accessible_project_ids`, `can_write_task`. Imports only `models`/`database`/`fastapi` — never a router |
| `api/assignment_history.py` | `snapshot`/`record` for the assignment history log (rule 1d). Imports only `models`. Read via `GET /api/tasks/{id}/assignment-history` (manager+), shown by `frontend/js/pages/project/assignment-history.js` (pure formatting in `assignment-history-format.js`) |
| `api/work_sessions.py` | Team-monitoring tracker: per user × task working stretches, fed by `note_time` (timer ping) and `note_save` (task save) with dict ops only, checkpointed by a background drain into `work_sessions`. Ships dark (`MONITOR_ENABLED`). Imports only `models`/`database`/`config`/`logging_service`. See `.devnotes/feature/team-monitoring/` |
| `api/rate_limit.py` | In-process sliding-window limiter (single-worker only, rule 9); used by add-member |
| `api/routers/` | One router per resource (projects, tasks, labels, teams, grants, time_logs, data, detect, auth, label_studio, exports, imports, attendance, image_info, thumbs, and — on `dev-stage` only — telemetry). `tasks.py` also holds the per-task detail endpoint, the review/assignment endpoints, and the in-process soft lock (`_TASK_LOCKS`). `team.py` is a deprecated alias for `time_logs.py` (F6) |
| `api/routers/teams.py` | Team CRUD and rosters — the *team* axis (who is in a team). Says nothing about project access |
| `api/routers/grants.py` | `/api/projects/{id}/grants` — the *access* axis (what a team may do on one project). Owner-only |
| `formats/` | Import/export format logic (COCO, task JSON, YOLO, masks), one module per format; pure, testable without a server. See docs/ARCHITECTURE.md § 2.1 |
| `formats/annotation_rows.py` | The dict ⇄ `Annotation` row mapping, and `sync_task_annotations*` — the diffing writer that makes a one-shape edit write one row. The single boundary between the wire format and storage |
| `detector.py` | ML model loading + inference (YOLO, SAM, CLIP) |
| `frontend/app.html` | The annotation canvas page. Markup only — its behaviour is `frontend/js/init.js` and the modules it imports |
| `frontend/js/canvas/` | The canvas engine, split by job: `interactions.js` (all pointer/key handling — the big one), `draw.js` (rendering), `geometry.js` (points, bounds, hit-testing), `view.js` (zoom/pan/drag state), `untangle.js` (polygon self-intersection: trim via `untangleRing`, split via `splitRing`), `merge.js` (ring union), `marquee.js`, `context-menu.js`, `comment-geometry.js`, `handle-size.js`. `untangle.js`/`merge.js`/`geometry.js` are pure and unit-tested under plain node (`tests/js/*_spec.mjs`) — keep them that way |
| `frontend/js/objects-filter.js` | Which rows the Objects panel lists (selection filter / hidden filter) and the hidden count. Pure: no DOM, no `state` import — filtering must never reach the saved annotation set (GOTCHAS #18) |
| `frontend/js/` | Shared ES modules — new frontend code goes here (`utils.js`, `state.js`, `task-lock.js`, `components/`, `pages/`) |
| `frontend/js/permissions.js` | Client-side role ranking. Deliberate mirror of `api/permissions.py`; **rendering only**, never a security boundary (rule 18b) |
| `frontend/js/task-status.js` | The task-status vocabulary and the approved group. Deliberate mirror of the status block in `schemas.py`; **rendering only** (rule 18b applies verbatim). Guarded by `tests/test_task_status.py` |
| `frontend/js/wipe-guard.js` | The wipe guard's rule and thresholds, mirrored from `config.py`/`tasks.py` (rule 11c). Pure; **rendering and pre-checks only** |
| `frontend/js/components/confirm-dialog.js` | Styled destructive confirm (Clear all, large deletes); resolves to a boolean |
| `frontend/js/session.js` | `getCurrentUser()` — real identity from `/api/auth/me`, cached per page load (rule 18c) |
| `frontend/js/canvas-permissions.js` | The canvas's whole permission surface: read-only mode, assignment banner, Approve/Reject. Keeps `init.js` from growing |
| `frontend/teams.html` + `js/pages/teams-list.js`, `js/pages/team/` | Teams list and the per-team shell (members / projects / settings) |
| `frontend/js/pages/project/access.js` | Project `#/access` — grant management, owner only |
| `scripts/` | Ops + one-off tooling: `migrate_sqlite_to_postgres.py`, `backup.py`, `schedule-backup.ps1`, `install-service.ps1`, `run.ps1`, `restore_drill.py`, `verify-resilience.ps1`, `health-check.ps1`, `schedule-health-check.ps1` |
| `.devnotes/deployment-hardening/` | Deployment audit, phased task list, the annotation-save-loss postmortem, and the resilience plan/implementation record (`06_RESILIENCE_PLAN.md`, `07_RESILIENCE_IMPLEMENTATION.md`) |
| `.devnotes/performance-fixes/` | Why annotations were normalised out of the blob, what was measured, the phased implementation record (`06_PROGRESS.md`) and the production runbook (`07_PRODUCTION_ROLLOUT.md`) |
| `.devnotes/teams/` | The Teams feature: design, schema, API/permission map, UI spec, 30 edge cases, phasing and the deviations actually made (`PLAN.md` §8) |
| `.devnotes/<slug>/` | ~45 more of these, one per feature or investigation (`fix-untangle/`, `bulk-loss-guard/`, `server-issue-diagnosis/`, `frontend-telemetry/`, `merge-objects/`, …). The rows above are only the ones worth reading before touching core systems. **Before changing any non-trivial subsystem, `ls .devnotes/` and read the matching folder** — it usually records what was already tried and why the obvious approach was rejected |
| `models/` | ML weight files (gitignored) — *not* Python code; `models.py` is the DB models |
| `alembic/` | Database migrations |
