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
backup robustness for the single-PC deployment).

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

### Backend

1. **All `/api/*` routes require auth** via `dependencies=[Depends(get_current_user)]` on the router — except `/api/auth/*`. This now holds across every router (the old `tasks.py`/`data.py`/`label_studio.py` gaps are closed). Any new router must include the auth dependency.
1a. **State-changing routers also require CSRF** via `Depends(require_csrf)` (see `api/auth.py`): a double-submit token check exempting pure `Authorization: Bearer` clients. Routers that mutate (e.g. `tasks.py`, `labels.py`) carry it; new mutating routers must too.
1b. **Authorization goes through `api/permissions.py`, never `owner_id` directly.** Use `require_project(pid, user, db, minimum=ProjectRole.X)` / `require_task(...)` with the minimum role the endpoint actually needs, and `accessible_project_ids(user, db)` for list endpoints. `get_owned_project` / `_get_owned_task` / `_owned_project_ids` survive only as deprecated aliases and are deleted in Phase 5 F5 — do not call them from new code. The per-call-site minimums are tabulated in `.devnotes/teams/03_API.md` §4.1; two that look wrong are deliberate (**exports at `reviewer`** — a read, but not one every annotator should one-click the whole dataset with; **imports at `manager`** — a replace-mode import wipes labels for everyone). Contract: **404** when the caller has no role at all (identical to a nonexistent id, so ids cannot be enumerated), **403** when they have a role but not a high enough one, with a message naming the role required.
1c. **Permission checks run before conflict detection**, always. A 403 must never surface as a 409 — a user who lacks permission needs an actionable message, not "someone else edited this". See `api/routers/tasks.py`'s update branch for the required ordering.
2. **Imports go at the top of the file.** Existing code has `import json` inside functions and `import schemas` mid-file — do not copy that.
3. **No bare `except:` and no silent `pass`.** Catch the specific exception, and either handle it meaningfully or log it. See CONVENTIONS.md § Errors.
4. **GET endpoints must not write to the database.**
5. **Use correct HTTP methods going forward:** `POST` create, `PATCH` update, `DELETE` delete. The existing `POST /api/projects/update` style is legacy; new endpoints must not follow it.
6. **Declare `response_model` with a Pydantic schema** for new endpoints instead of returning hand-built dicts (e.g. `TaskDetail` on `GET /api/tasks/{id}`).
7. **Datetimes:** always timezone-aware UTC — `datetime.now(timezone.utc)`, never `datetime.utcnow()` (deprecated, returns naive datetimes).
8. **Schema changes go through Alembic** (`alembic revision --autogenerate`), not by relying on `Base.metadata.create_all` (which only creates missing tables, never alters existing ones). The migration chain must build the schema on an **empty** database (Postgres deploys start empty) — do not write migrations that only `ALTER` pre-existing tables.
9. **Never touch `JOBS`/`_models`/`_TASK_LOCKS` from a second process/worker.** These are in-process dicts (the AI job queue, loaded ML models, and the soft task lock in `tasks.py`). The app must run as exactly one uvicorn worker until this state is moved out of process. **Do not add `--workers N`.**
10. **Concurrent-write commits use `commit_with_retry(db)`** (from `database.py`), not raw `db.commit()` — it backs off on lock/deadlock/serialization contention. All router commits route through it.
11. **Task save conflict model:** writes carry a per-tab `client_id`; `tasks.last_client_id` records the last writer. A conflict (409) is only raised when a *different* client wrote since the caller read — a client never conflicts with itself. Do not reintroduce a timestamp-only check, and never disable client-side saving on a 409. See `.devnotes/deployment-hardening/04_ANNOTATION_SAVE_LOSS.md`.
11a. **The task-status vocabulary lives in `schemas.py`, and approval is a *group*.** `APPROVED_STATUSES` ("Approved", "Verified", "Checked", "Passed") are synonyms differing only in which **export batch** a sign-off belongs to — the team approves each week under a fresh name so an export can select just the new work instead of re-shipping everything ever approved. `REVIEW_STATUSES` (reviewer-gated) and `TERMINAL_STATUSES` (demote-on-edit) are *derived* from it, as are the interop mapping (`formats/common.py`), the review verbs and the completion statistics. **Adding a batch status is one line in `APPROVED_STATUSES` plus its verb in `ReviewActionLiteral`** (a Pydantic `Literal` cannot be built from a variable; an import-time assert catches the drift) and one line in `frontend/js/task-status.js`. Never test a status by name where the group is meant — `status == "Approved"` silently excludes three batches. Completion means *signed off*: `Completed` is the annotator's submission and is counted as `awaiting_review`, not as done.
12. **Configuration comes from `config.py`**, which loads `.env` for every entry point (uvicorn, alembic, scripts) — never read `os.environ` for deployment settings elsewhere, and never rely on the launcher script to have loaded `.env`.

### Frontend

13. **All frontend code lives in ES modules under `frontend/js/`**, imported from the page scripts. The old `frontend/app.js` monolith is gone — the canvas page is fully decomposed (`init.js` is the entry point, with `components/`, `canvas/`, `pages/` beneath it); do not recreate a catch-all file. Module imports are version-pinned (`./foo.js?v=1`); a JS change that clients must pick up needs a hard reload (content-hashing is a deferred item — see tasks.md D4). **Bump the pin at *every* import site of a changed module, plus the entry `<script>` tag in the page** — a partial bump ships clients a mix of old and new modules.
14. **Auth state lives in the httpOnly cookie.** `localStorage['logged_in']` is only a UI hint for redirects — never treat it as security.
15. **Modals:** toggle with `classList.add/remove('is-active')`, never `style.display` (CSS transitions depend on the class — see `.agents/AGENTS.md`).
16. All backend calls from authenticated pages go through the `apiFetch` wrapper (handles 401 → redirect), not raw `fetch`.
17. **Per-task annotation loading.** The gallery list is fetched annotation-free (`include_annotations=false`); annotations hydrate per task on open via `GET /api/tasks/{id}`. Do not go back to loading every task's annotations up front.
18. **Unsaved work is protected by a per-task localStorage draft** (`draftKey(taskId)` in `state.js`), restored on task open and cleared only on server-confirmed save. There is deliberately no cross-tab `storage` listener reloading annotations, and no single global draft slot. See 04_ANNOTATION_SAVE_LOSS.md.
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

- Branch from `main`: `feat/<slug>`, `fix/<slug>`, `docs/<slug>`.
- Commits: imperative summary line ≤ 72 chars, conventional prefix (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, `test:`).
- Before pushing: run the app locally (`venv\Scripts\uvicorn.exe main:app --port 8000`, or `scripts/run.ps1` which loads `.env`) and exercise the feature; run `pytest` if tests exist for the area.
- Full workflow: `docs/DEVELOPMENT_GUIDE.md`.

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
| `api/rate_limit.py` | In-process sliding-window limiter (single-worker only, rule 9); used by add-member |
| `api/routers/` | One router per resource (projects, tasks, labels, teams, grants, time_logs, data, detect, auth, label_studio, exports, imports). `tasks.py` also holds the per-task detail endpoint, the review/assignment endpoints, and the in-process soft lock (`_TASK_LOCKS`). `team.py` is a deprecated alias for `time_logs.py` (F6) |
| `api/routers/teams.py` | Team CRUD and rosters — the *team* axis (who is in a team). Says nothing about project access |
| `api/routers/grants.py` | `/api/projects/{id}/grants` — the *access* axis (what a team may do on one project). Owner-only |
| `formats/` | Import/export format logic (COCO, task JSON, YOLO, masks), one module per format; pure, testable without a server. See docs/ARCHITECTURE.md § 2.1 |
| `detector.py` | ML model loading + inference (YOLO, SAM, CLIP) |
| `frontend/app.html` | The annotation canvas page. Markup only — its behaviour is `frontend/js/init.js` and the modules it imports |
| `frontend/js/objects-filter.js` | Which rows the Objects panel lists (selection filter / hidden filter) and the hidden count. Pure: no DOM, no `state` import — filtering must never reach the saved annotation set (GOTCHAS #18) |
| `frontend/js/` | Shared ES modules — new frontend code goes here (`utils.js`, `state.js`, `task-lock.js`, `components/`, `pages/`) |
| `frontend/js/permissions.js` | Client-side role ranking. Deliberate mirror of `api/permissions.py`; **rendering only**, never a security boundary (rule 18b) |
| `frontend/js/task-status.js` | The task-status vocabulary and the approved group. Deliberate mirror of the status block in `schemas.py`; **rendering only** (rule 18b applies verbatim). Guarded by `tests/test_task_status.py` |
| `frontend/js/session.js` | `getCurrentUser()` — real identity from `/api/auth/me`, cached per page load (rule 18c) |
| `frontend/js/canvas-permissions.js` | The canvas's whole permission surface: read-only mode, assignment banner, Approve/Reject. Keeps `init.js` from growing |
| `frontend/teams.html` + `js/pages/teams-list.js`, `js/pages/team/` | Teams list and the per-team shell (members / projects / settings) |
| `frontend/js/pages/project/access.js` | Project `#/access` — grant management, owner only |
| `scripts/` | Ops + one-off tooling: `migrate_sqlite_to_postgres.py`, `backup.py`, `schedule-backup.ps1`, `install-service.ps1`, `run.ps1`, `restore_drill.py`, `verify-resilience.ps1`, `health-check.ps1`, `schedule-health-check.ps1` |
| `.devnotes/deployment-hardening/` | Deployment audit, phased task list, the annotation-save-loss postmortem, and the resilience plan/implementation record (`06_RESILIENCE_PLAN.md`, `07_RESILIENCE_IMPLEMENTATION.md`) |
| `.devnotes/teams/` | The Teams feature: design, schema, API/permission map, UI spec, 30 edge cases, phasing and the deviations actually made (`PLAN.md` §8) |
| `models/` | ML weight files (gitignored) — *not* Python code; `models.py` is the DB models |
| `alembic/` | Database migrations |
