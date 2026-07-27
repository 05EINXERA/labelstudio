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
list; Phases 0–3 done, Phase 4 load-testing pending), `04_ANNOTATION_SAVE_LOSS.md`
(the save-loss bug and its fix — the model that makes concurrent editing safe).

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
12. **Configuration comes from `config.py`**, which loads `.env` for every entry point (uvicorn, alembic, scripts) — never read `os.environ` for deployment settings elsewhere, and never rely on the launcher script to have loaded `.env`.

### Frontend

13. **New frontend code goes in ES modules under `frontend/js/`**, imported from the page scripts. Do not add more code to `frontend/app.js` (4,500-line monolith being decomposed) unless you are wiring in a module. Module imports are version-pinned (`./foo.js?v=1`); a JS change that clients must pick up needs a hard reload (content-hashing is a deferred item — see tasks.md D4).
14. **Auth state lives in the httpOnly cookie.** `localStorage['logged_in']` is only a UI hint for redirects — never treat it as security.
15. **Modals:** toggle with `classList.add/remove('is-active')`, never `style.display` (CSS transitions depend on the class — see `.agents/AGENTS.md`).
16. All backend calls from authenticated pages go through the `apiFetch` wrapper (handles 401 → redirect), not raw `fetch`.
17. **Per-task annotation loading.** The gallery list is fetched annotation-free (`include_annotations=false`); annotations hydrate per task on open via `GET /api/tasks/{id}`. Do not go back to loading every task's annotations up front.
18. **Unsaved work is protected by a per-task localStorage draft** (`draftKey(taskId)` in `state.js`), restored on task open and cleared only on server-confirmed save. There is deliberately no cross-tab `storage` listener reloading annotations, and no single global draft slot. See 04_ANNOTATION_SAVE_LOSS.md.

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
| `api/routers/` | One router per resource (projects, tasks, labels, team, data, detect, auth, label_studio, exports, imports). `tasks.py` also holds the per-task detail endpoint and the in-process soft lock (`_TASK_LOCKS`) |
| `formats/` | Import/export format logic (COCO, task JSON, YOLO, masks), one module per format; pure, testable without a server. See docs/ARCHITECTURE.md § 2.1 |
| `detector.py` | ML model loading + inference (YOLO, SAM, CLIP) |
| `frontend/app.html` + `app.js` | The annotation canvas page (the monolith) |
| `frontend/js/` | Shared ES modules — new frontend code goes here (`utils.js`, `state.js`, `task-lock.js`, `components/`, `pages/`) |
| `scripts/` | Ops + one-off tooling: `migrate_sqlite_to_postgres.py`, `backup.py`, `schedule-backup.ps1`, `install-service.ps1`, `run.ps1` |
| `.devnotes/deployment-hardening/` | Deployment audit, phased task list, and the annotation-save-loss postmortem |
| `models/` | ML weight files (gitignored) — *not* Python code; `models.py` is the DB models |
| `alembic/` | Database migrations |
