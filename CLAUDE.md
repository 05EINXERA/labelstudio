# CLAUDE.md — Project Instructions

## What this project is

A browser-based image annotation workspace ("mini Label Studio"). Users create
projects, upload images as tasks, and draw bounding boxes / polygons on an
HTML5 canvas. AI assistance comes from local models: YOLOv8 / YOLO-World
(auto-detect), Meta SAM (magic-wand segmentation), and CLIP (auto-tagging).

- **Backend:** FastAPI (Python) + SQLAlchemy + SQLite (WAL mode). Entry point: `main.py`.
- **Frontend:** Vanilla JS + HTML5 Canvas, served as static files from `frontend/`. No build step, no framework.
- **ML:** `detector.py` loads and runs the models. Inference runs through an in-process background job queue (`api/routers/detect.py`).
- **Deploy target:** single server (Render.com), one uvicorn process.

Read `docs/ARCHITECTURE.md` before moving code between modules,
`docs/CONVENTIONS.md` before writing new code, and `docs/GOTCHAS.md` before
copying any existing pattern — several existing patterns are known mistakes.

## Rules for AI assistants and developers

These are prescriptive. Where existing code disagrees with a rule, the rule
wins; fix the old code opportunistically when you touch it, and never copy the
old pattern into new code.

### Backend

1. **All `/api/*` routes require auth** via `dependencies=[Depends(get_current_user)]` on the router — except `/api/auth/*`. Currently `tasks.py`, `data.py`, and `label_studio.py` are unauthenticated; that is a bug, not a convention. Any new router must include the auth dependency.
2. **Imports go at the top of the file.** Existing code has `import json` inside functions and `import schemas` mid-file — do not copy that.
3. **No bare `except:` and no silent `pass`.** Catch the specific exception, and either handle it meaningfully or log it. See CONVENTIONS.md § Errors.
4. **GET endpoints must not write to the database.** (`get_project_metrics` currently does — known bug, see GOTCHAS.md.)
5. **Use correct HTTP methods going forward:** `POST` create, `PATCH` update, `DELETE` delete. The existing `POST /api/projects/update` style is legacy; new endpoints must not follow it.
6. **Declare `response_model` with a Pydantic schema** for new endpoints instead of returning hand-built dicts.
7. **Datetimes:** always timezone-aware UTC — `datetime.now(timezone.utc)`, never `datetime.utcnow()` (deprecated, returns naive datetimes).
8. **Schema changes go through Alembic** (`alembic revision --autogenerate`), not by relying on `Base.metadata.create_all` (which only creates missing tables, never alters existing ones).
9. **Never touch `JOBS` from a second process/worker.** The AI job queue is an in-process dict; the app must run as exactly one uvicorn worker unless the queue is replaced.

### Frontend

10. **New frontend code goes in ES modules under `frontend/js/`**, imported from the page scripts. Do not add more code to `frontend/app.js` (4,500-line monolith being decomposed) unless you are wiring in a module.
11. **Auth state lives in the httpOnly cookie.** `localStorage['logged_in']` is only a UI hint for redirects — never treat it as security.
12. **Modals:** toggle with `classList.add/remove('is-active')`, never `style.display` (CSS transitions depend on the class — see `.agents/AGENTS.md`).
13. All backend calls from authenticated pages go through the `apiFetch` wrapper (handles 401 → redirect), not raw `fetch`.

### Repo hygiene

14. **Never commit:** model weights (`*.pt`, `*.onnx`), `workspace.db*`, `uploads/`, `.jwt_secret`, or any credentials. `.jwt_secret` was committed historically and must be treated as compromised (see GOTCHAS.md #1).
15. **One-off/debug scripts go in `scripts/`**, not the repo root, and are never named `test_*.py` (that prefix is reserved for pytest). Root files `test_sam_mask.py`, `test_upload.py`, `check_endpoints.py`, `debug_hang.py` are legacy manual scripts, not tests.
16. **Real tests live in `tests/`** and run with pytest. New backend endpoints and bug fixes should come with a test.
17. New dependencies must be added to `requirements.txt` with a version constraint in the same commit that introduces the import.

### Workflow

- Branch from `main`: `feat/<slug>`, `fix/<slug>`, `docs/<slug>`.
- Commits: imperative summary line ≤ 72 chars, conventional prefix (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, `test:`).
- Before pushing: run the app locally (`venv\Scripts\uvicorn.exe main:app --port 8765`) and exercise the feature; run `pytest` if tests exist for the area.
- Full workflow: `docs/DEVELOPMENT_GUIDE.md`.

## Key file map

| Path | What it is |
|---|---|
| `main.py` | FastAPI app assembly: middleware, router mounting, static files |
| `config.py` | `DATA_DIR` env config (persistent disk location) |
| `database.py` | SQLAlchemy engine/session, SQLite WAL pragmas, `get_db` |
| `models.py` | SQLAlchemy ORM models (database tables) |
| `schemas.py` | Pydantic request/response schemas |
| `api/auth.py` | JWT creation/validation, password hashing, `get_current_user` |
| `api/routers/` | One router per resource (projects, tasks, labels, team, data, detect, auth, label_studio, exports, imports) |
| `formats/` | Import/export format logic (COCO, task JSON, YOLO, masks), one module per format; pure, testable without a server. See docs/ARCHITECTURE.md § 2.1 |
| `detector.py` | ML model loading + inference (YOLO, SAM, CLIP) |
| `frontend/app.html` + `app.js` | The annotation canvas page (the monolith) |
| `frontend/js/` | Shared ES modules (`utils.js`) — new frontend code goes here |
| `models/` | ML weight files (gitignored) — *not* Python code; `models.py` is the DB models |
| `alembic/` | Database migrations |
