# Architecture

How the system is put together today, where each kind of new code belongs, and
the structural problems to fix before the codebase grows around them.

---

## 1. The system in one picture

```
Browser (vanilla JS + Canvas, native ES modules — no bundler)
  ├─ index.html / dashboard.html / project_details.html / app.html
  └─ frontend/js/init.js  ← module entry point, loaded by app.html
        ├─ canvas/     draw.js, geometry.js, interactions.js, view.js
        ├─ components/ workspace.js (render+persist+labels), timer.js
        ├─ export/     coco.js, csv.js
        ├─ ai/         detect.js, shared.js, detect-state.js
        └─ state.js, api.js, dom.js, utils.js, timer-state.js, comment-overlay.js
        │  fetch /api/*  (JWT in httpOnly cookie)
        ▼
FastAPI (main.py, ONE uvicorn worker)
  ├─ api/auth.py            JWT + bcrypt, get_current_user dependency
  ├─ api/routers/*          one router per resource
  │     ├─ projects, tasks, labels, team, data   → SQLAlchemy → SQLite (WAL)
  │     ├─ detect            → in-process job dict → detector.py (threads)
  │     └─ label_studio      → external Label Studio server (SDK)
  ├─ detector.py            loads YOLO / SAM / CLIP, runs inference
  └─ StaticFiles            serves frontend/ and DATA_DIR/uploads/
        │
        ▼
Disk (DATA_DIR, default ".")
  ├─ workspace.db (+ -wal/-shm)   all persistent state
  ├─ uploads/                     task images (uuid-named)
  └─ models/detector/, models/wand/   ML weights (auto-downloaded)
```

Request flows worth understanding:

- **CRUD:** browser → router → SQLAlchemy session (`get_db`) → SQLite. Responses are hand-built dicts (legacy; new code uses Pydantic `response_model`).
- **AI inference:** browser POSTs to `/api/detect[/...]` → server returns a `job_id` immediately → the work runs in a FastAPI `BackgroundTasks` thread writing into the module-level `JOBS` dict → browser polls `/api/detect/status/{job_id}` until completed/failed. This exists so a 30-second SAM inference doesn't time out the HTTP request. The result is deleted from `JOBS` on first successful poll.
- **Concurrency control:** task saves carry the `updated_at` the client last saw; the server 409s if the row is newer (optimistic locking). SQLite WAL mode + a 15 s busy timeout make concurrent reads/writes safe within a single process.

---

## 2. Where new code belongs

| You are adding… | Put it in… |
|---|---|
| A new API resource (e.g. comments) | `api/routers/comments.py` + models in `models.py` + schemas in `schemas.py` + an Alembic migration; mount in `main.py` |
| An endpoint on an existing resource | That resource's router file |
| Logic shared by two routers | A module under `api/` (like `api/auth.py`) |
| A new ML capability | `detector.py` for now (see § 3.5) + a job runner in `api/routers/detect.py` |
| Frontend behavior for the annotation page | A new ES module in `frontend/js/` (or an existing one whose responsibility matches — see § 3.1), imported by `init.js` or by the module that needs it |
| A new page | `frontend/<page>.html` + `frontend/<page>.js`, mounted automatically by StaticFiles |
| A one-off/debug script | `scripts/` |
| A real automated test | `tests/` |

The dependency direction to preserve: **routers → (models, schemas, database,
detector); never the reverse.** `detector.py` must not import from `api/`;
`models.py`/`schemas.py` must not import routers. Keeping the arrows one-way is
what lets you test the lower layers without a running server.

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
| `state.js` | The single `state` object, workspace constants, pure label lookups |
| `dom.js` | Stable (never-reassigned) DOM element references |
| `canvas/view.js` | Mutable canvas view-state (pan/zoom/drag/hover) |
| `canvas/geometry.js` | Pure geometry/color math over annotation shapes |
| `canvas/draw.js` | The 3-layer canvas renderer |
| `canvas/interactions.js` | Mouse/keyboard-driven annotation editing — the densest module; touch with care |
| `components/workspace.js` | Render orchestration, autosave/persistence, label lifecycle, import/export triggers, Label Studio panel — one file because these are mutually recursive, not a design choice |
| `components/timer.js` | Session timer UI + backend time sync |
| `export/coco.js`, `export/csv.js` | Client-side export builders |
| `ai/detect.js`, `ai/shared.js`, `ai/detect-state.js` | Auto-detect, auto-tag, magic-wand job orchestration |
| `timer-state.js`, `comment-overlay.js` | Small shared-mutable-state carriers between two otherwise-separate modules |
| `init.js` | Entry point — imports every module above, plus everything not yet modularized (gallery, sidebar projects, settings/team modals, panel drag-and-drop) and the page's bootstrap sequence |

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

### 3.2 Auth is enforced inconsistently — security hole

`projects`, `detect`, `labels`, `team` require a login; **`tasks`, `data`, and
`label_studio` do not**. Anyone who can reach the server can read/modify/delete
every task and the shared workspace blob without logging in.

**Direction:** add `dependencies=[Depends(get_current_user)]` to the three
unprotected routers (check the frontend sends credentials on those calls —
`apiFetch` already handles 401s). This is a small diff and should be done
immediately.

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

### 3.4 The in-process job queue caps the app at one worker

`JOBS = {}` in `detect.py` lives in one Python process. Run uvicorn with
`--workers 2` and polls will land on a worker that has never heard of the
job_id → spurious 404s. It also leaks: a job whose client never polls (closed
tab) stays in the dict forever, holding full inference results in memory.

**Direction:** acceptable for the current single-worker deployment, but (a)
add a TTL sweep that evicts finished jobs older than ~10 minutes, and (b) if
multi-worker or multi-machine scaling is ever needed, replace with jobs stored
in SQLite (status + result columns) — same polling API, no shared-memory
assumption. Never "fix" scaling by just adding workers.

### 3.5 `detector.py` mixes five concerns in 800 lines

Model downloading/conversion, path resolution, three different model
families (YOLO, SAM, CLIP), image decoding, and geometry post-processing share
one file with six module-level lock/singleton pairs.

**Direction:** split into `ml/weights.py` (download/resolve),
`ml/yolo.py`, `ml/sam.py`, `ml/clip.py`, `ml/images.py` (decode/validate)
when next doing significant ML work. The router-facing functions
(`detect_objects`, `segment_point`, `classify_image`) keep their signatures.

### 3.6 Two competing persistence models on the frontend

Newer pages talk to real endpoints (`/api/projects`, `/api/tasks`). The older
path (`sync.js`) mirrors a whole-workspace JSON blob from `localStorage` into
the unauthenticated `/api/data` key-value table — via a monkey-patched
`localStorage.setItem` and a synchronous XHR that blocks page load. Both
systems store overlapping data, and the blob is shared by *all* users (last
writer wins, no locking).

**Direction:** treat `/api/data` + `sync.js` as legacy. Migrate whatever the
annotation page still reads from the blob onto real endpoints, then delete
`sync.js` and the `data` router. Until then: never add a new `SYNC_KEYS`
entry.

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

- **Single uvicorn worker.** Required by the `JOBS` dict and sensible for SQLite. Don't add `--workers N`.
- **SQLite is fine at this scale.** WAL mode + short transactions handle a small annotating team. Don't introduce Postgres until there's a measured need — it would complicate the Render deploy and local setup for everyone.
- **No frontend build step is deliberate.** Plain ES modules keep setup at "run uvicorn, open browser". Adopting a bundler/framework is a team decision, not something to sneak in with a feature.
- **`DATA_DIR` indirection matters.** All persistent writes (db, uploads, downloaded weights) must go under `DATA_DIR`, because in production that's the mounted persistent disk — writes anywhere else vanish on redeploy.
