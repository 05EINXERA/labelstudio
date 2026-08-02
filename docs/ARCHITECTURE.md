# Architecture

How the system is put together today, where each kind of new code belongs, and
the structural problems to fix before the codebase grows around them.

---

## 1. The system in one picture

```
Browser (vanilla JS + Canvas, native ES modules — no bundler)
  ├─ index.html / projects.html / project.html / teams.html / app.html
  └─ frontend/js/init.js  ← module entry point, loaded by app.html
        ├─ canvas/     draw.js, geometry.js, interactions.js, view.js
        ├─ components/ workspace.js (render+persist+labels), timer.js
        ├─ export/     coco.js, csv.js
        ├─ ai/         detect.js, shared.js, detect-state.js
        └─ state.js, api.js, dom.js, utils.js, timer-state.js, comment-overlay.js
        │  fetch /api/*  (JWT in httpOnly cookie)
        ▼
FastAPI (main.py, ONE uvicorn worker)
  ├─ api/auth.py            JWT + bcrypt, get_current_user dependency  (authentication: who are you?)
  ├─ api/permissions.py     the role resolver                          (authorization: what may you do?)
  ├─ api/routers/*          one router per resource, ALL require auth (see § 3.2)
  │     ├─ projects, tasks, labels, teams, grants, time_logs, data
  │     │                    → SQLAlchemy → SQLite (dev) or Postgres (LAN deploy)
  │     ├─ detect            → in-process job dict → detector.py (threads)
  │     └─ label_studio      → external Label Studio server (SDK)
  ├─ detector.py            loads YOLO / SAM / CLIP, runs inference
  └─ StaticFiles            serves frontend/ and DATA_DIR/uploads/
        │
        ▼
Disk / DB (DATA_DIR, default ".", or a separate Postgres instance)
  ├─ workspace.db (+ -wal/-shm)       SQLite path — dev only
  ├─ Postgres database                 LAN-deployment path — see config.IS_SQLITE
  ├─ uploads/                          task images (uuid-named), always under DATA_DIR
  └─ models/detector/, models/wand/    ML weights (auto-downloaded), always under DATA_DIR
```

Which persistence backend is live is controlled by `config.DATABASE_URL` /
`config.IS_SQLITE` — see § 4. `uploads/` and the ML weight cache are always
plain files under `DATA_DIR` regardless of which DB backend is active.

Request flows worth understanding:

- **CRUD:** browser → router → SQLAlchemy session (`get_db`) → SQLite or Postgres, chosen by `config.IS_SQLITE`. Responses are hand-built dicts (legacy; new code uses Pydantic `response_model`).
- **AI inference:** browser POSTs to `/api/detect[/...]` → server returns a `job_id` immediately → the work runs in a FastAPI `BackgroundTasks` thread writing into the module-level `JOBS` dict → browser polls `/api/detect/status/{job_id}` until completed/failed. This exists so a 30-second SAM inference doesn't time out the HTTP request. The result is deleted from `JOBS` on first successful poll.
- **Concurrency control:** task saves carry the `updated_at`/`client_id` the client last saw; the server 409s only on a genuine cross-client conflict (see CLAUDE.md rule 11, `.devnotes/deployment-hardening/04_ANNOTATION_SAVE_LOSS.md`). SQLite WAL mode + a busy timeout, or Postgres's own MVCC, make concurrent reads/writes safe within the single uvicorn worker.
- **Authorization:** every project- or task-scoped route resolves the caller's role through `api/permissions.py` before doing anything else — owner short-circuit, then the maximum over grants reachable via the caller's team memberships, then an `org`-visibility floor. `None` → 404 (indistinguishable from a nonexistent id), too low → 403. Permission checks run **before** conflict detection so a 403 never surfaces as a 409. See `.devnotes/teams/01_DESIGN.md` § 6.

---

## 2. Where new code belongs

| You are adding… | Put it in… |
|---|---|
| A new API resource (e.g. comments) | `api/routers/comments.py` + models in `models.py` + schemas in `schemas.py` + an Alembic migration; mount in `main.py` |
| An endpoint on an existing resource | That resource's router file |
| An import/export format | A module in `formats/` (see § 2.1); wire it into `exports.py` / `imports.py` |
| Logic shared by two routers | A module under `api/` (like `api/auth.py`, `api/permissions.py`) |
| An authorization rule, or a new role | `api/permissions.py` — never inline in a router, and never a second `owner_id` comparison. Mirror any ranking change into `frontend/js/permissions.js` (§ 3.1) |
| A new ML capability | `detector.py` for now (see § 3.5) + a job runner in `api/routers/detect.py` |
| Frontend behavior for the annotation page | A new ES module in `frontend/js/` (or an existing one whose responsibility matches — see § 3.1), imported by `init.js` or by the module that needs it |
| A new page | `frontend/<page>.html` + `frontend/<page>.js`, mounted automatically by StaticFiles |
| A one-off/debug script | `scripts/` |
| A real automated test | `tests/` |

The dependency direction to preserve: **routers → (models, schemas, database,
detector, formats, api/permissions); never the reverse.** `detector.py` and
`formats/` must not import from `api/`; `models.py`/`schemas.py` must not import
routers. Keeping the arrows one-way is what lets you test the lower layers
without a running server.

`api/permissions.py` sits in that lower layer deliberately: it imports only
`models`, `database` and `fastapi`, and **nothing from `api/routers/`**. Routers
import it; it never imports them. That is what makes the resolver testable
against a bare session (`tests/test_permissions.py` drives it with no
TestClient) and what stops authorization logic dispersing back into the routes.
The one-line summary of the layering:

```
api/routers/*  →  api/permissions.py  →  models / database
                  api/auth.py         →  models / database
```

`api/auth.py` answers *who are you*; `api/permissions.py` answers *what may you
do*. They are separate modules because they change for different reasons — a new
role touches only the second.

---

## 2.1 The `formats/` package

Import/export format logic lives in `formats/`, a top-level package peer to
`api/` and `detector.py`. The routers (`exports.py`, `imports.py`) stay
HTTP-shaped — request validation, the job queue, the download handler, task
matching, label resolution — and everything that knows what a COCO file or a
YOLO label file *looks like* lives in `formats/`.

| Module | Format | Direction |
|---|---|---|
| `common.py` | shared helpers: geometry, `value_from_name`, status maps, `image_size`, `annotation_type_of` | — |
| `coco.py` | COCO JSON | build + parse |
| `annotations_json.py` | task JSON (single array or per-task) | build + parse |
| `yolo.py` | YOLOv8 segmentation | build + parse |
| `masks.py` | rasterized masks (semantic/instance × direct/index) | **build only** |
| — | flat CSV | build only (still inline in `exports.py`) |

Everything in `formats/` is pure or takes an explicit `Session`/`Task` — no
FastAPI, no request state — so each piece is unit-testable without a
TestClient. That is the reason the logic was lifted out of the routers.

Two contracts hold this together:

- **Export builders** return `List[(arcname, bytes)]` for archive formats, or a
  serialized string for single-file ones. A builder never constructs a ZIP
  itself; `exports.py` owns the container (`_build_zip` for prefixed
  multi-folder archives, `_zip_entries` for a format that owns its whole
  layout, like YOLO).
- **Parsers** return `{filename: [annotation, ...]}`. YOLO is the exception
  that shaped the pipeline: its coordinates are normalized to `[0, 1]` and
  can't be scaled to pixels until the file is matched to a task, so the parser
  leaves them normalized and `imports.py` denormalizes in the apply step.

Some formats can't represent every task (YOLO and masks need image
dimensions). Rather than silently drop them, a builder returns a `skipped`
list of `{filename, reason}`, threaded through the job status to the export UI.

Masks are **export-only** by decision, not omission — a raster mask can't be
traced back to the source polygons faithfully, and it carries no trustworthy
class identity. See `.devnotes/data-refactor/00_FORMAT_ANALYSIS.md` § 8 before
adding a mask parser.

---

## 3. Structural problems to fix before the codebase grows

Listed in priority order. Each one gets more expensive the longer the code
grows around it.

### 3.1 Frontend module layout

The annotation page is native ES modules under `frontend/js/`, loaded via
`<script type="module" src="js/init.js">` in `app.html` — no bundler, no
build step. Dependency direction is one-way: lower layers never import from
higher ones, and nothing imports back into `components/workspace.js` (see the
table below and `.devnotes/refactor/MODULE_MAP.md` for the authoritative,
per-file export/consumer list).

| Module | Owns |
|---|---|
| `utils.js` | Pure helpers — UUIDs, formatting, no DOM/state |
| `api.js` | `apiFetch` wrapper (401 handling) + job-status polling |
| `permissions.js` | Role ranking + `atLeast`/`canReview`/`canManage`. **Deliberate mirror of `api/permissions.py`** (no build step can share one definition); rendering only, never a security boundary |
| `session.js` | `getCurrentUser()` — real identity from `/api/auth/me`, cached per page load and shared by concurrent callers |
| `state.js` | The single `state` object, workspace constants, pure label lookups |
| `dom.js` | Stable (never-reassigned) DOM element references |
| `canvas/view.js` | Mutable canvas view-state (pan/zoom/drag/hover) |
| `canvas/geometry.js` | Pure geometry/color math over annotation shapes |
| `canvas/draw.js` | The 3-layer canvas renderer |
| `canvas/interactions.js` | Mouse/keyboard-driven annotation editing — the densest module; touch with care |
| `components/workspace.js` | Render orchestration, autosave/persistence, label lifecycle, import/export triggers, Label Studio panel — one file because these are mutually recursive, not a design choice |
| `components/timer.js` | Session timer UI + backend time sync |
| `components/modal.js` | Modal open/close + inline field errors. Exists so three screens do not each re-invent it and reintroduce `style.display` (rule 15) |
| `components/app-nav.js` | The "Projects \| Teams" header nav + shared logout, rendered from one array |
| `components/role-badge.js`, `components/team-picker.js` | Role pills; the teams `<select>` reused by three screens |
| `canvas-permissions.js` | The canvas's whole permission surface — read-only mode, assignment banner, Approve/Reject. Separate module so `init.js` gained ~50 lines instead of fifty scattered edits |
| `export/coco.js`, `export/csv.js` | Client-side export builders |
| `ai/detect.js`, `ai/shared.js`, `ai/detect-state.js` | Auto-detect, auto-tag, magic-wand job orchestration |
| `timer-state.js`, `comment-overlay.js` | Small shared-mutable-state carriers between two otherwise-separate modules |
| `init.js` | Entry point — imports every module above, plus everything not yet modularized (gallery, sidebar projects, settings/team modals, panel drag-and-drop) and the page's bootstrap sequence |

Pages outside the canvas follow the same conventions but have their own entry
points, each a hash router over lazily-loaded views exporting
`mount(root, ctx)` / `unmount()`:

| Page | Entry | Views |
|---|---|---|
| `projects.html` | `pages/projects-list.js` | — (single table) |
| `project.html` | `pages/project/router.js` | `home`, `tasks`, `classes`, `imports`, `exports`, `access` |
| `teams.html` | `pages/teams-entry.js` | list, or `pages/team/router.js` → `members`, `projects`, `settings` |

Both routers **re-check the caller's role inside `renderRoute`**, not only when
rendering the nav: a hidden tab does nothing about a typed, bookmarked or shared
URL, and a role can be revoked after a link was saved. A disallowed route
resolves to the default one and the address bar is normalised to match, so it
never lies about which view is on screen.

**Why some state lives in a plain object instead of a variable:** ES module
imports are read-only bindings — a module can mutate an imported object's
*properties*, but can't reassign the imported name itself. Anything shared
across modules that needs reassignment (not just mutation) is therefore
wrapped in an object (`view`, `timerState`, `detectState`,
`commentOverlayRefs`) rather than exported as a loose `let`.

**What's still unmodularized, on purpose:** `init.js` still contains gallery
management, sidebar project listing, and several settings/team modals — none
of that was in scope for the module extraction and it's a reasonable next
target when someone next touches those areas. New code touching those
features can extract its module as part of that change; it doesn't need a
separate cleanup pass first.

### 3.2 Auth coverage — closed

**Status: fixed.** Every `/api/*` router now carries
`dependencies=[Depends(get_current_user)]` except `/api/auth/*` itself — see
CLAUDE.md rule 1 and `.devnotes/deployment-hardening/01_AUDIT.md` P1-1. This
section previously described `tasks`, `data`, and `label_studio` as
unauthenticated; that gap is closed and any new router must include the same
dependency.

**Authorization is a separate axis and is also closed.** Authentication only
establishes *who* the caller is; until the Teams feature, "what may they do"
was a single `Project.owner_id` comparison repeated at 28 call sites. Those all
now resolve through `api/permissions.py` (§ 2), each stating the minimum role
it needs. `get_owned_project` / `_get_owned_task` / `_owned_project_ids` remain
only as deprecated aliases so no site could be missed during the migration;
they are deleted in `.devnotes/teams/07_PHASING.md` F5. Do not call them from
new code.

Two properties worth preserving when touching any of this:

- **404 for "no role", 403 for "role too low".** A project you cannot reach is
  indistinguishable from one that does not exist, which is what stops id
  enumeration. Once you *can* see it, hiding a permission failure behind a 404
  would be a bug report rather than security, so the 403 names the role needed.
- **Permission checks precede conflict detection.** A 403 must never surface as
  a 409; see the ordering in `api/routers/tasks.py`'s update branch and
  CLAUDE.md rule 1c.

### 3.3 The flat root should become a package before it grows

`main.py`, `config.py`, `database.py`, `models.py`, `schemas.py`,
`detector.py` all sit at the repo root next to scripts, stray text files, and
the venv. It works at 6 modules; at 15 it's soup, and root-level name
collisions are already biting (`models.py` the ORM file vs. `models/` the
weights directory — genuinely confusing to every newcomer).

**Direction (when someone has a free afternoon, as a single dedicated PR):**

```
app/
  main.py  config.py  database.py
  db_models.py         # renamed from models.py
  schemas.py
  ml/detector.py       # split per § 3.5
  api/auth.py  api/routers/...
scripts/               # check_endpoints, debug_hang, manual test scripts
tests/
frontend/
model_weights/         # renamed from models/, keeps .pt/.onnx out of the name clash
```

Don't do this file-by-file across feature PRs — a half-moved layout is worse
than either endpoint.

### 3.4 In-process state caps the app at one worker

Three plain Python dicts live in one process and would not be shared across
workers: `JOBS` (`detect.py` — AI inference job status/results), `_models`
(`detector.py` — loaded ML model singletons), and `_TASK_LOCKS`
(`api/routers/tasks.py` — the soft per-task open/heartbeat lock, TTL 60s).
Run uvicorn with `--workers 2` and, for `JOBS`, polls will land on a worker
that has never heard of the job_id → spurious 404s; for `_TASK_LOCKS`, two
workers would each think they own the lock. `JOBS` also leaks: a job whose
client never polls (closed tab) stays in the dict forever, holding full
inference results in memory. See CLAUDE.md rule 9 — none of this is
persisted across a process restart either (a crash or supervised restart
silently drops all three; annotation data itself is DB-committed and
unaffected).

**Direction:** acceptable for the current single-worker deployment, but (a)
add a TTL sweep that evicts finished jobs older than ~10 minutes, and (b) if
multi-worker or multi-machine scaling is ever needed, replace `JOBS` with
jobs stored in SQLite/Postgres (status + result columns) — same polling API,
no shared-memory assumption — and move `_TASK_LOCKS` into the DB too. This is
tracked as the deferred D3 item in `.devnotes/deployment-hardening/tasks.md`.
Never "fix" scaling by just adding workers.

### 3.5 `detector.py` mixes five concerns in 800 lines

Model downloading/conversion, path resolution, three different model
families (YOLO, SAM, CLIP), image decoding, and geometry post-processing share
one file with six module-level lock/singleton pairs.

**Direction:** split into `ml/weights.py` (download/resolve),
`ml/yolo.py`, `ml/sam.py`, `ml/clip.py`, `ml/images.py` (decode/validate)
when next doing significant ML work. The router-facing functions
(`detect_objects`, `segment_point`, `classify_image`) keep their signatures.

### 3.6 Legacy whole-blob sync — removed

**Status: fixed.** The old `sync.js` path (a whole-workspace JSON blob
mirrored from `localStorage` into `/api/data` via a monkey-patched
`localStorage.setItem`) has been removed — `tasks.md` T3.1: the `<script
src="sync.js">` tag was deleted from `app.html`. `/api/data` itself is no
longer a shared, unauthenticated, last-writer-wins table either: it now
requires auth and is scoped per-user (`owner_id`), per `01_AUDIT.md` A-5a and
`tasks.md` T3.1. This section previously described both as live hazards;
they are not. Do not reintroduce a whole-blob sync path — new frontend state
goes through real per-resource endpoints.

### 3.7 Schema management is split-brain

`main.py` runs `Base.metadata.create_all()` on startup *and* Alembic exists
with one initial migration. `create_all` only creates missing tables — it
never adds columns — so once real users have databases, column additions
made "the easy way" will silently not apply to them.

**Direction:** all future schema changes ship as Alembic migrations, and
startup should run migrations (or at minimum, developers run
`alembic upgrade head` after pulling). Keep `create_all` only as a
convenience for a brand-new empty database.

### 3.8 Repo hygiene debris

`parsed_content.txt`, `parsed_content_utf8.txt`, `messt.jpg` (0 bytes), and a
committed `.jwt_secret` (see GOTCHAS #1) are in git; `.gitignore` contains
corrupted mojibake lines; `requirements.txt` pins `python-multipart` twice
with conflicting constraints and omits packages the code imports
(`ultralytics`, `transformers`, `requests` for scripts). Small stuff, but it
teaches newcomers that the repo is a junk drawer. One cleanup PR fixes all of
it.

---

## 4. Constraints to respect (not bugs — load-bearing decisions)

- **Single uvicorn worker.** Required by the `JOBS` dict. Don't add `--workers N`.
- **SQLite for local/dev, Postgres for the LAN deployment.** SQLite (WAL mode + short transactions) is fine for a single developer, but the current ~20-25 person LAN deployment runs Postgres on the same box — see CLAUDE.md and `config.IS_SQLITE`. Don't assume SQLite is the production target when reasoning about scale, pooling, or backups.
- **No frontend build step is deliberate.** Plain ES modules keep setup at "run uvicorn, open browser". Adopting a bundler/framework is a team decision, not something to sneak in with a feature.
- **`DATA_DIR` indirection matters.** All persistent writes (db, uploads, downloaded weights) must go under `DATA_DIR`, because in production that's the mounted persistent disk — writes anywhere else vanish on redeploy.
