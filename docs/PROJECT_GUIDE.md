# Project Guide — Image Annotation Workspace

## 1. What this is

A self-hosted, browser-based image annotation workspace (a lightweight in-house alternative to Label Studio, with an optional bridge *to* Label Studio). Annotators upload images into projects, draw boxes/polygons on an HTML5 canvas, and use three AI assists — auto-detect, magic-wand segmentation, and auto-tagging — to avoid drawing everything by hand. Output is exported as COCO JSON or CSV.

Despite the repo name `labelstudio`, this is **not** a fork of Label Studio. It's an original FastAPI + vanilla-JS app that can optionally push annotations into a real Label Studio instance via the official SDK.

## 2. Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI ([main.py](main.py)), Uvicorn |
| ORM / DB | SQLAlchemy 2.x → SQLite (`workspace.db`), WAL mode |
| Migrations | Alembic ([alembic/](alembic/)) |
| Auth | JWT (python-jose, HS256) in an httpOnly cookie; bcrypt hashing |
| Frontend | Vanilla JS + HTML5 Canvas, no build step, no framework |
| Inference | OpenCV DNN (ONNX YOLOv8), Ultralytics (YOLO-World, SAM/MobileSAM), HF Transformers (CLIP, SAM2), PyTorch |
| Deploy | Render ([render.yaml](render.yaml)) with a 5 GB persistent disk at `/data` |

Python 3.12 locally (`venv/`); Render pins 3.10.12.

## 3. Architecture

```
frontend/ (static, served by FastAPI itself)
  index.html      → login / register
  dashboard.html  → project list + metrics
  project_details.html → tasks in a project
  app.html        → the annotation canvas shell
  js/init.js      → module entry point (ES modules, no build step)
    js/canvas/    → view, geometry, draw, interactions
    js/components/ → workspace (render+persist+labels), timer
    js/export/    → coco, csv
    js/ai/        → detect, shared, detect-state
    js/state.js, api.js, dom.js, utils.js, ...
  sync.js         → localStorage ↔ /api/data mirror
        │  fetch (cookie auth)
        ▼
main.py  ── cache-control middleware ── CORS ── routers
        │
api/routers/  auth, projects, tasks, team, labels, data, detect, label_studio
        │                                          │
   database.py (SQLAlchemy/SQLite)          detector.py (30 KB ML core)
                                                   │
                                            models/ + *.pt weights
```

Three things worth knowing up front:

**Static serving is order-sensitive.** `main.py` mounts `frontend/` at `/` *last*, after all API routers and `/uploads`. Any new router must be included before that mount or it gets shadowed.

**Caching is `no-store` across the board.** The middleware in [main.py](main.py#L19-L33) sends `no-store, no-cache, must-revalidate` on every HTML/CSS/JS response — nothing is long-cached, so frontend edits always show up on a normal refresh. No manual cache-busting is required.

**AI runs through a job queue, not a request.** `POST /api/detect` returns a `job_id` immediately and does the work in a FastAPI `BackgroundTasks`; the client polls `GET /api/detect/status/{job_id}`. The store is an in-process dict (`JOBS` in [api/routers/detect.py](api/routers/detect.py#L11)) and results are **deleted on first read**. Consequences: single-worker only, jobs die on restart, and two pollers racing means one gets a 404.

## 4. Data model ([models.py](models.py))

| Table | Notes |
|---|---|
| `projects` | name, slug, type, status, creator, assignee. Status auto-derives from task completion in the metrics endpoint. |
| `tasks` | one row per uploaded image. `image_path`, `status`, `assignee`, `time_spent` (seconds), `updated_at`, and `annotations` — **a JSON blob in a TEXT column**, not a table. |
| `team_members` | name (PK) + `time_logged`. Separate from `users`. |
| `labels` | id/name/color — the global class palette. |
| `users` | username + bcrypt hash. Auth identities. |
| `workspace_data` | opaque key→value store backing `sync.js`. |

Two deliberate consequences of the JSON-blob design: you cannot query "all polygons with label X" in SQL, and every annotation save rewrites the whole blob. That's why concurrency needs the check below.

**Optimistic locking**: `POST /api/tasks` compares the client's `updated_at` against the row's; if the server is >1s newer it returns **409** ([api/routers/tasks.py](api/routers/tasks.py#L51-L59)). The frontend is expected to surface a refresh prompt.

**`users` vs `team_members` are unrelated tables.** Login identity and workload/time-tracking rosters are tracked separately — creating a user does not create a team member.

## 5. AI features ([detector.py](detector.py))

All three are lazily loaded behind locks, cached in module globals, and use CUDA if available.

**Auto-Detect** — `detect_objects()`. Default path is YOLOv8-seg as **ONNX through OpenCV DNN** (no torch needed at inference). `ensure_model_file()` downloads the `.pt` from the Ultralytics GitHub release and, if only a `.pt` exists, calls `ultralytics` to export it to ONNX on first run. If `prompts` are supplied, it switches to **YOLO-World** (`yolov8s-worldv2.pt`) for open-vocabulary detection. Sizes n/s/m/l/x via `model_size`.

**Magic Wand** — `segment_point()`. Point/box prompts → polygon. First tries a shortcut: if `prompt` is a COCO class, it runs the normal detector and reuses that mask if the click falls inside. Otherwise it runs SAM — `mobile_sam.pt` by default via Ultralytics, or `facebook/sam2-hiera-large` via Transformers (picks the highest-IoU of SAM2's 3 candidate masks). Masks → `findContours` → `approxPolyDP` with a `precision` epsilon the UI exposes.

**Auto-Tag** — `classify_image()`. CLIP `openai/clip-vit-base-patch32`, zero-shot over the 80 COCO classes plus scene tags (daytime, indoor, screenshot, …).

Guardrails: `MAX_IMAGE_BYTES` 50 MB, `MAX_IMAGE_PIXELS` 50 M — both env-tunable.

## 6. Exports

Client-side only, in `frontend/js/export/` — the browser builds the file and triggers a download; nothing hits the server.

- **COCO JSON** — `buildCocoExport()` ([frontend/js/export/coco.js](frontend/js/export/coco.js)) → `dataset_annotations.json`. Groups annotations into images/annotations/categories.
- **CSV** — flat one-row-per-annotation → `dataset_annotations.csv`.
- **Class list** — exports just the label palette as JSON.
- **Label Studio push** — `POST /api/label-studio/send` is the one server-side path: creates a task (or annotates an existing one) on a remote LS instance using `label-studio-sdk`. Requires `LABEL_STUDIO_URL` + `LABEL_STUDIO_API_KEY`; returns 400 if the key is unset.

## 7. Authentication

Register/login at `/api/auth/register` and `/api/auth/token` (OAuth2 password form). Both set `access_token` as an **httpOnly, samesite=lax cookie** and also return the token in the body. `get_token()` accepts either the cookie or an `Authorization: Bearer` header, so scripts and the browser share one path. Tokens last 7 days.

The signing secret resolves in order: `JWT_SECRET` env → `.jwt_secret` file in CWD → generated and written to that file. There is no roles/permissions layer — every authenticated user sees everything.

**Protection is per-router and currently uneven.** `projects`, `team`, `labels`, and `detect` declare `dependencies=[Depends(get_current_user)]`. **`tasks`, `data`, and `label_studio` do not** — they are reachable unauthenticated. Given `tasks` carries all annotation content and `data` is a read/write KV store, treat this as a real gap rather than a design choice, and fix it before any non-local deployment.

## 8. Management & tracking

- **Metrics** — `GET /api/projects/{id}/metrics` and `/api/projects/metrics/batch` (batch version exists to avoid N+1 from the dashboard). Returns total/completed/progress/comment counts, and writes back the derived project status as a side effect of a GET.
- **Time tracking** — the client sends `time_spent_delta` on task saves (accumulated server-side) and posts to `/api/team/time` for the per-member roll-up.
- **Bulk ops** — `POST /api/tasks/bulk-delete` and `/bulk-update` for assignee/status.
- **Uploads** — `POST /api/projects/{id}/upload`, multi-file. Extension-allowlisted to png/jpg/jpeg/gif/webp, renamed to a UUID, stored under `$DATA_DIR/uploads`, one Task row created per file.

## 9. Known rough edges

Worth knowing before you touch these areas:

- `JOBS` being in-process means **do not scale to multiple Uvicorn workers** without moving it to Redis or similar.
- Deleting a project deletes its tasks manually in Python; there are no DB-level cascades or FK constraints beyond `tasks.project_id`.
- Repo root holds stray artifacts — `parsed_content*.txt`, `messt.jpg` (0 bytes), `debug_hang.py`, `check_endpoints.py`, `server.py` (a single comment).
- **`.jwt_secret` is committed to the repo.** Rotate it before deploying anywhere real, and add it to `.gitignore`.
- `render.yaml` ships `JWT_SECRET: "change-me-in-production"` — change it.
- Model weights (`.pt`, `.onnx`) are gitignored; see the Setup Guide for how they arrive.
