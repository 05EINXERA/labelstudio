# Label Studio Architecture Overview

## System Components

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          FRONTEND (Vanilla JS)                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  app.html (Annotation Canvas)                                           │
│    ├─ frontend/js/init.js (Entry point)                                 │
│    ├─ frontend/js/state.js (Global state + statusLocked, isProjectOwner)│
│    ├─ frontend/js/canvas/                                               │
│    │   ├─ draw.js (Rendering)                                           │
│    │   ├─ geometry.js (Math)                                            │
│    │   ├─ interactions.js (Canvas events + group/ungroup guards) ⭐     │
│    │   └─ view.js (Viewport state)                                      │
│    ├─ frontend/js/components/                                           │
│    │   ├─ workspace.js (Save/sync + lock guards) ⭐                     │
│    │   ├─ gallery.js (Task loading + ownership detection) ⭐            │
│    │   ├─ mode-controls.js (UI buttons + lock state) ⭐                 │
│    │   ├─ timer.js (Session tracking)                                   │
│    │   └─ zoom-control.js (Viewport zoom)                               │
│    ├─ frontend/js/ai/ (YOLO, SAM, CLIP detection)                       │
│    ├─ frontend/js/export/ (Client-side export logic)                     │
│    └─ frontend/js/utils.js, api.js, dom.js                              │
│                                                                           │
│  project.html (Project Management)                                      │
│    ├─ frontend/pages/project/ (Tasks, Classes, Imports, Exports)        │
│    └─ frontend/pages/teams.js (Team management)                         │
│                                                                           │
│  index.html / dashboard.html (Project list, auth)                       │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                    apiFetch() + JWT (httpOnly cookie)
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         BACKEND (FastAPI)                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  main.py (ASGI app, middleware, static files)                           │
│  config.py (Environment-based config, DATABASE_URL resolution)          │
│  database.py (SessionLocal, commit_with_retry)                          │
│  models.py (SQLAlchemy ORM)                                             │
│  schemas.py (Pydantic request/response)                                 │
│                                                                           │
│  api/auth.py (JWT, password hashing, CSRF, get_current_user)            │
│  api/routers/                                                            │
│    ├─ auth.py (Login, logout, registration)                             │
│    ├─ projects.py (Create, read, update, delete)                        │
│    ├─ tasks.py (CRUD + soft locking)                                    │
│    │   └─ Includes: status lock guards ⭐ (rule 11: client_id conflict) │
│    ├─ labels.py (Class management)                                      │
│    ├─ team.py (Team members, permissions)                               │
│    ├─ detect.py (YOLO, SAM inference jobs)                              │
│    ├─ exports.py (COCO, YOLO, CSV, masks export)                        │
│    ├─ imports.py (COCO, YOLO import)                                    │
│    ├─ data.py (Legacy endpoints)                                        │
│    └─ label_studio.py (External Label Studio sync)                      │
│                                                                           │
│  detector.py (ML model loading/inference: YOLO, SAM, CLIP)              │
│  ml/ (ML subsystem packages)                                             │
│    ├─ yolo.py, sam.py, clip.py                                          │
│    ├─ weights.py (Model caching)                                        │
│    ├─ images.py (Image preprocessing)                                   │
│    └─ common.py (Shared ML utilities)                                   │
│                                                                           │
│  formats/ (Import/export logic)                                         │
│    ├─ coco.py (COCO JSON)                                               │
│    ├─ yolo.py (YOLO format)                                             │
│    ├─ annotations_json.py (Task JSON)                                   │
│    ├─ masks.py (Mask rendering)                                         │
│    └─ common.py (Shared format utilities)                               │
│                                                                           │
│  StaticFiles (Serves frontend/ and DATA_DIR/uploads/)                   │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                        SQLAlchemy ORM
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     DATABASE LAYER                                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  SQLite (Development)                PostgreSQL (Production LAN)        │
│    ├─ workspace.db                     ├─ annotation (database)          │
│    ├─ -wal (Write-Ahead Log)           ├─ users (table)                  │
│    └─ -shm (Shared memory)             ├─ projects (table)               │
│                                         ├─ tasks (table + indexes)       │
│  Selected via config.IS_SQLITE          ├─ annotations (table)           │
│                                         ├─ export_jobs (table) ⭐       │
│                                         ├─ teams, team_members           │
│                                         ├─ labels, workspace_data        │
│                                         └─ (Alembic migrations applied)  │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   PERSISTENT STORAGE (Disk)                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  DATA_DIR (default ".")                                                 │
│    ├─ workspace.db / Postgres socket                                    │
│    ├─ uploads/ (Task images, uuid-named)                                │
│    ├─ models/ (ML weights, auto-downloaded)                             │
│    ├─ backups/ (Database backups, resilience)                           │
│    ├─ logs/ (Application logs)                                          │
│    └─ .jwt_secret (JWT signing key, gitignored)                         │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Key Request Flows

### 1. Annotation Save (Task Lock Feature) ⭐

```
Browser: Canvas edit → Click Save
    │
    ├─ state.statusLocked = true (locked status)?
    ├─ state.isProjectOwner = true (owner)?
    │
    └─→ syncToBackend({ annotations, status })
         │
         ├─ Guard: if (statusLocked && !isProjectOwner && !targetStatus) → return false
         │
         └─→ PATCH /api/tasks/{id}
              │
              ├─ get_current_user (JWT in httpOnly cookie)
              ├─ Guard in tasks.py: if task.status in LOCKED_STATUSES
              │                       and has_annotation_changes
              │                       and not trying_to_unlock
              │                     → 403 Forbidden
              │
              └─→ db.commit() with retry logic
                   └─ Clear local draft on success
```

### 2. Task Load (Ownership Detection) ⭐

```
Browser: Click task in gallery
    │
    └─→ switchImage(index)
         │
         ├─ GET /api/projects/{projectId}
         │  └─ Set state.isProjectOwner = (project.creator == username)
         │
         ├─ GET /api/tasks/{taskId}
         │  └─ Set state.statusLocked = (status in ["Completed", "Approved", "Verified"])
         │
         ├─ updateModeControlsLockState()
         │  └─ Disable buttons if (statusLocked && !isProjectOwner)
         │
         └─ render()
```

### 3. Export Job Processing

```
Browser: Click "Download Export"
    │
    └─→ POST /api/exports
         │
         ├─ validate export format/options
         ├─ create job_id in export_jobs table
         └─ background_tasks.add_task(_run_export_job)
              │
              ├─ Query tasks with optional status filter
              ├─ Build export (COCO/YOLO/CSV/masks)
              ├─ Encode to ZIP if multi-format
              └─ Update job: status = "completed", content = <bytes>
                   │
                   ▼ (Browser polling)
    GET /api/exports/{job_id}
         │
         ├─ Check job.status = "completed"
         ├─ Return Response(content, media_type, headers={"Content-Disposition": ...})
         └─ One-shot download (delete job after)
```

### 4. AI Detection (Background Job)

```
Browser: Click "Detect Objects"
    │
    └─→ POST /api/detect/autodetect
         │
         ├─ create job_id
         ├─ background_tasks.add_task(_run_detect)
         │  └─ Load YOLO model (cached in detector.MODELS)
         │  └─ Run inference
         │  └─ Store results in JOBS[job_id] dict
         │
         └─→ Return { job_id }
              │
              ▼ (Browser polling every 100ms)
    GET /api/detect/status/{job_id}
         │
         ├─ Check JOBS[job_id].status
         └─ Return { status, annotations }
              (delete JOBS[job_id] on first poll)
```

---

## New Features (This Sprint) ⭐

### 1. Task Status Locking

**Problem:** Completed/Approved tasks should be immutable without explicit unlocking.

**Solution:**
- Added `LOCKED_STATUSES = {"Completed", "Approved", "Verified"}` in `tasks.py`
- Backend guard in `_update_or_create_task_impl`: reject 403 if locked + annotation changes
- Frontend guard in `syncToBackend` / `manualSaveWithUI`: skip saves on locked tasks
- Canvas interaction guard: reject draw/edit events if locked
- Exception: Status-only changes and panning always allowed; project owners bypass lock entirely

**Files Changed:**
- `api/routers/tasks.py` (line 29, 424+)
- `frontend/js/state.js` (line 58, 154)
- `frontend/js/components/gallery.js` (line 171-174, 219)
- `frontend/js/components/workspace.js` (line 77-79, 219-221)
- `frontend/js/components/mode-controls.js` (line 135-154)
- `frontend/js/canvas/interactions.js` (line 614)

### 2. Project Owner Override

**Problem:** Owners should bypass locks to edit completed work.

**Solution:**
- Added `isProjectOwner` flag to state
- Determine ownership on workspace load by comparing `project.creator` with logged-in username
- All lock guards check: `if (statusLocked && !isProjectOwner)` before rejecting
- Buttons enabled for owners even when task is locked

**Files Changed:**
- `frontend/js/state.js` (line 59)
- `frontend/js/init.js` (line 211-229)
- `frontend/js/components/workspace.js` (line 77, 219)
- `frontend/js/components/mode-controls.js` (line 142)
- `frontend/js/canvas/interactions.js` (line 614)

### 3. Export Jobs Table

**Problem:** Exports failed because `export_jobs` table didn't exist.

**Solution:**
- Created Alembic migration `49021da7f1d9_add_export_jobs_table.py`
- Table stores: id, status, media_type, filename, content (blob), meta_info, error, created_at
- Applied successfully to PostgreSQL via `alembic upgrade head`
- One-shot downloads (job deleted after download)

**Database Schema:**
```sql
CREATE TABLE export_jobs (
    id VARCHAR(36) PRIMARY KEY NOT NULL,
    status VARCHAR(32) NOT NULL,
    media_type VARCHAR(128),
    filename VARCHAR(255),
    content BLOB,
    meta_info TEXT,
    error TEXT,
    created_at TIMESTAMP DEFAULT now()
)
```

---

## Configuration

### Environment Variables (.env)

```bash
APP_ENV = "production"                    # or "development"
APP_HOST = "0.0.0.0"                      # 127.0.0.1 for dev
APP_PORT = "8000"
DATABASE_URL = "postgresql://user:pass@host:5435/annotation"  # or omit for SQLite
IS_SQLITE = False                         # (auto-detected from DATABASE_URL)
JWT_SECRET = "64-char-hex-string"
CORS_ORIGINS = "http://localhost:8000"
DATA_DIR = "."                            # Path for uploads/, models/, backups/
```

### Database Selection

- **Development:** `DATABASE_URL` unset → SQLite (`./workspace.db`)
- **Production (LAN):** `DATABASE_URL = "postgresql://..."` → Postgres

All schema changes via Alembic migrations (`alembic upgrade head`).

---

## Concurrency & Safety

### Task Save Conflict Model (Rule 11)

Prevents annotation loss under concurrent edits:
- Each client gets a `client_id` (UUID)
- Tasks store `last_client_id` (who wrote last)
- On save: if `last_client_id != client_id`, check if annotations actually differ
  - **No conflict** if same client (edit again in same tab)
  - **Conflict** (409) only if different client changed annotations since read
  - Draft recovery: local localStorage saves work not yet acknowledged by server

### Status Lock Conflict Prevention

- Status changes always succeed (even when locked)
- Annotation changes + locked status = 403 (except owner)
- Guards at two layers:
  1. **Frontend:** UI/canvas blocks interactions, skip saves
  2. **Backend:** Reject via 403 if locked client sends annotations

This prevents accidental edits while still allowing intentional unlocking.

---

## Directory Structure

```
labelstudio/
├── app/                    (Moved to package)
│   ├── config.py          (Env-based config)
│   ├── database.py        (SQLAlchemy setup)
│   ├── models.py          (ORM models)
│   ├── schemas.py         (Pydantic schemas)
│   └── logging_config.py
├── api/
│   ├── auth.py
│   └── routers/
│       ├── tasks.py       (Status lock guards)
│       ├── projects.py
│       ├── exports.py     (Export jobs)
│       ├── detect.py      (AI inference)
│       └── ...
├── formats/               (Import/export logic)
├── ml/                    (YOLO, SAM, CLIP)
├── frontend/
│   ├── app.html          (Annotation canvas)
│   ├── project.html      (Project page)
│   ├── js/
│   │   ├── init.js       (Entry point, ownership detection)
│   │   ├── state.js      (statusLocked, isProjectOwner)
│   │   ├── canvas/       (Draw, interactions with lock guards)
│   │   ├── components/   (Workspace, gallery, mode-controls)
│   │   ├── ai/           (Detection)
│   │   ├── export/       (Export logic)
│   │   └── pages/        (Dashboard, project pages)
│   └── css/              (Styles)
├── tests/                (Unit + integration tests)
├── scripts/              (Ops tooling, one-offs)
├── alembic/              (Database migrations)
│   └── versions/         (Migration files)
├── main.py               (FastAPI app assembly)
├── config.py             (Config wrapper)
├── database.py           (DB layer)
├── models.py             (ORM models)
├── schemas.py            (Pydantic schemas)
├── detector.py           (ML inference facade)
└── .env                  (gitignored, loaded by config.py)
```

---

## Key Design Principles

1. **One uvicorn worker** — No `--workers N`. Shared in-process state: JOBS (detection), MODELS (ML), _TASK_LOCKS (soft lock).
2. **One database path** — SQLite (dev) or Postgres (prod) via config.IS_SQLITE.
3. **Auth on all `/api/*`** — except `/api/auth/*`.
4. **CSRF on mutations** — POST/PATCH/DELETE require token via Depends(require_csrf).
5. **Pydantic response_model** — New endpoints return typed schemas, not hand-built dicts.
6. **No bare except** — Specific exceptions, log meaningful errors.
7. **Migrations on empty DB** — Schema must build from `alembic upgrade head` on a fresh database.
8. **Frontend modules (ES)** — No bundler, version-pinned imports, reusable components.
9. **Conflict model (client_id)** — Prevents annotation loss; timestamps are secondary.
10. **Draft persistence** — Per-task localStorage, cleared only on server ack.
11. **Status lock** — Completed/Approved/Verified immutable unless owner changes status.

---

## Next Steps / Roadmap

See `.devnotes/deployment-hardening/tasks.md` (Phases 0–4 done):

- **D1–D4:** Content hashing for browser cache-busting, Mask rendering/bundling
- **Future:** Multi-worker scaling, real-time collaboration, advanced permissions

