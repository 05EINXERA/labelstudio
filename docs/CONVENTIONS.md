# Coding Conventions

This is the standard for all new code in this repo. The existing code was
written fast, as an MVP, and does **not** always follow these rules. Where a
rule overrides current practice, it says so. When you touch old code, bring it
up to this standard; never copy an old pattern just because it's already there.

Written for developers who may be new to professional practice — each rule has
a short "do / don't" example and a one-line reason. For the deeper reasoning
behind each section — useful when you hit a case the rules don't cover — see
[PRINCIPLES.md](PRINCIPLES.md).

---

## 1. Naming

### Python

- **Files/modules:** `snake_case.py` (lowercase with underscores). ✅ `label_studio.py` ❌ `LabelStudio.py`
- **Functions and variables:** `snake_case`. ✅ `get_project_metrics` ❌ `getProjectMetrics`
- **Classes:** `PascalCase` (each word capitalized, no underscores). ✅ `TeamMember` ❌ `team_member`
- **Constants** (values set once at import and never changed): `UPPER_SNAKE`. ✅ `MAX_IMAGE_BYTES` ❌ `maxImageBytes`
- **Loop variables get real names.** ❌ `for l in labels` (looks like the digit 1) ✅ `for label in labels`

### JavaScript

- **Variables and functions:** `camelCase`. ✅ `const savedFiles` ❌ `const saved_files`
  - *Override:* backend JSON uses `snake_case` keys (e.g. `time_spent`). Keep the key as-is when reading API data (`task.time_spent`), but any variable you create is camelCase. Don't rename API fields on one side only — that's how sync bugs happen.
- **DOM element variables:** name after what it is, ending with the element kind. ✅ `autoDetectButton`, `classesList` (current code does this well — keep it).
- **Files:** `camelCase.js` or short lowercase (`utils.js`, `sync.js`).

### API routes

- Paths are lowercase, plural nouns, kebab-case for multi-word: `/api/projects`, `/api/label-studio`.
- **Query parameters are `snake_case` going forward.** *Override:* the code currently mixes `projectId` (camelCase query param) and `include_annotations` (snake_case). New parameters use snake_case to match Python; existing camelCase params stay until deliberately migrated (renaming them silently breaks the frontend).

---

## 2. File and folder structure

- One router file per resource in `api/routers/` (this is already the pattern — keep it). A "resource" is one kind of thing the API manages: projects, tasks, labels.
- Shared backend logic that two routers need goes in a module under `api/` (e.g. `api/auth.py`), never copy-pasted between routers.
- Import/export format logic goes in `formats/` (see ARCHITECTURE.md § 2.1), one module per format, not inline in `exports.py`/`imports.py`. Adding a format is: a module in `formats/` exposing `build`/`parse`, a row in `EXPORT_FORMATS`, and a branch in the export job + import dispatcher. Reuse `formats/common.py` for geometry and value derivation — never re-derive a label `value` with a local `.replace()` chain (it can't see cross-class collisions; see GOTCHAS § 17). If a format can't represent every task, return a `skipped` list rather than dropping tasks silently.
- New frontend logic goes in ES modules under `frontend/js/`, one concern per file. Check `.devnotes/refactor/MODULE_MAP.md` for what each existing module already owns before creating a new one — logic often belongs in an existing file (e.g. a new label-related helper goes in `state.js` or `components/workspace.js`, not a new file). **Do not add new logic to `frontend/js/init.js`** unless it's wiring/bootstrap or genuinely belongs to one of the not-yet-modularized areas (gallery, sidebar, settings modals) it still contains.
- One-off scripts (debugging, data fixes, experiments) go in `scripts/`, never the repo root. *Override:* `test_upload.py`, `test_sam_mask.py`, `check_endpoints.py`, `debug_hang.py` currently sit at the root; that was a mistake (see § 7 for why `test_*` names are especially bad).
- Never commit generated or local files: databases (`workspace.db*`), model weights, `uploads/`, secrets. If you're unsure whether a file belongs in git, ask: "could my teammate regenerate this, and does it change every run?" If yes to either, gitignore it.

---

## 3. Error handling

**The rule: an error should either be handled (the code genuinely recovers) or
be visible (logged and/or returned as an HTTP error). Never both invisible and
unhandled.**

- **Never write bare `except:` or `except Exception: pass`.** It hides every possible bug — typos, wrong types, disk-full — not just the one you meant to tolerate.

  ```python
  # ❌ Don't (this exists in tasks.py today — it's a known mistake):
  try:
      annotations_data = json.loads(t.annotations)
  except:
      pass

  # ✅ Do — name the exception, and log so bad data is discoverable:
  try:
      annotations_data = json.loads(t.annotations)
  except json.JSONDecodeError:
      logger.warning("Task %s has corrupt annotations JSON; returning empty", t.id)
      annotations_data = []
  ```

- **In API endpoints, expected failures raise `HTTPException`** with the right status code: 400 (bad input), 401 (not logged in), 404 (doesn't exist), 409 (conflict — someone else changed the data). Unexpected failures: let them propagate — FastAPI returns a 500 and the traceback lands in the server log where you can find it.
- **Don't swallow the real error and return a vague one.** `label_studio.py` currently catches `Exception as e` and returns `"Label Studio sync failed."` without ever logging `e` — the operator has no way to learn *why* it failed. Log the exception (`logger.exception(...)`) even when you return a generic message to the client.
- **Client-input errors get their own exception type.** `detector.py` defines `DetectionClientError` for "the user sent something invalid" vs. "our code broke" — this is a good existing pattern; follow it.
- **Frontend:** every `fetch` chain needs a failure path the *user* can see (a status message or toast), not just `console.error`. A silent failed save is the worst bug an annotation tool can have — the user loses work and doesn't know.

---

## 4. Writing functions

- **A function does one thing, stated by its name.** If the name needs "and" (`update_status_and_recalculate`), split it.
- **Endpoints stay thin.** An endpoint function should: validate input → call the logic → shape the response. When query/update logic exceeds ~20 lines or is needed twice, move it to a plain function the endpoint calls. Beginner benefit: plain functions can be tested without running a web server.
- **No side effects that the name doesn't announce.** *Override:* `get_project_metrics` currently *updates* the project's status inside a GET handler. A reader (and HTTP caches, and browser prefetchers) assume GET reads only. Reads read; writes go in POST/PATCH handlers.
- **Return early instead of nesting.**

  ```python
  # ❌ Don't:
  if db_project:
      ... 20 lines ...
      return {"status": "ok"}
  raise HTTPException(404)

  # ✅ Do:
  if db_project is None:
      raise HTTPException(status_code=404, detail="Project not found")
  ... 20 lines at one indent level ...
  ```

- **Type hints on all new Python functions** (`def get_task(task_id: int) -> Task | None:`). They are documentation the editor checks for you.
- **Use Pydantic `response_model` on new endpoints** instead of hand-building dicts. *Override:* most current endpoints build `{"id": p.id, "name": p.name, ...}` by hand — every new field must then be added in several places, and one missed spot is a silent bug. Define a schema once in `schemas.py` and let FastAPI serialize.

---

## 5. State management

### Backend

- **The SQLite database is the single source of truth.** Anything that must survive a restart or be seen by another user lives in a table, accessed through a session from `get_db`.
- **In-memory state (like the `JOBS` dict in `detect.py`) is acceptable only for short-lived, losable data**, and it pins the app to one uvicorn worker. If you add in-memory state, write a comment saying what happens to it on restart, and make sure losing it is OK.
- **Concurrent edits use optimistic locking:** the client sends the `updated_at` it last saw; the server rejects with 409 if the row changed since. This exists for tasks — extend the same pattern to other user-edited resources rather than inventing a new one.
- **Datetimes are timezone-aware UTC.** ✅ `datetime.now(timezone.utc)` ❌ `datetime.utcnow()` (deprecated; produces "naive" datetimes that crash or silently mis-compare when mixed with aware ones).

### Frontend

- **Server data belongs to the server.** Fetch it, render it, send changes back. Don't build a second copy in `localStorage` that can drift.
  *Override:* `sync.js` currently monkey-patches `localStorage.setItem` to mirror a workspace blob to the server with a synchronous XHR (which freezes the page while it runs). This is legacy: do not extend it, do not add new `SYNC_KEYS`. New features talk to the API directly via `apiFetch`.
- `localStorage` is fine for *preferences* (theme, last-selected tool) — things where a stale value is harmless.
- **Auth truth is the httpOnly cookie**, which JS cannot read. `localStorage['logged_in']` is only a hint so pages can redirect before the first 401. Never gate an action on it.

---

## 6. Database & migrations

- Schema changes = an Alembic migration (`alembic revision --autogenerate -m "add X"` then review the generated file). *Override:* the code currently leans on `Base.metadata.create_all()` in `main.py`, which creates missing tables but **silently ignores** new columns on existing tables — teammates' databases will diverge from yours without errors.
- New JSON-blob columns (like `Task.annotations`) need a stated shape: document the expected structure in a comment on the model.

---

## 7. Tests

- **Real tests live in `tests/`, named `test_<module>.py`, run with `pytest`.** Test functions are `test_<behavior>` — the name states the expectation: ✅ `test_upload_rejects_exe_files` ❌ `test_1`.
- **The `test_` prefix is reserved for pytest.** *Override:* the root files `test_upload.py` and `test_sam_mask.py` are manual scripts that hit a live server / load a real model — pytest will try to collect and run them, and they'll fail or hang. Scripts belong in `scripts/` under a name like `manual_upload_check.py`.
- Backend tests use FastAPI's `TestClient` with a temporary SQLite database — never the real `workspace.db`.
- **What to test, in priority order:** (1) every bug you fix gets a test reproducing it, (2) endpoint success + main failure case (404/400/401), (3) pure logic functions. Don't test the framework itself (no test that "FastAPI returns JSON").
- Keep ML-model tests separate and skippable (`@pytest.mark.slow`) — they download gigabytes and shouldn't run on every push.

---

## 8. API design (new endpoints)

- `GET` reads, `POST` creates, `PATCH` partially updates, `DELETE` deletes. *Override:* the current API uses POST-for-everything (`POST /api/projects/update`, one `POST /api/tasks` that both creates and updates). Existing routes stay until deliberately migrated (the frontend depends on them), but new endpoints follow REST verbs.
- Deleting or updating a thing that doesn't exist returns **404**, not `{"status": "ok"}`. *Override:* `delete_task` and `delete_team_member` currently return ok unconditionally, so a typo'd ID looks like success.
- Every new router gets `dependencies=[Depends(get_current_user)]` unless it is `/api/auth`.
