# Upgrade Plan — Gap Analysis vs. `docs/comparison/image-annotation-tool-requirements.md`

*Produced 2026-07-19 by direct source analysis. No `discovery.md` exists anywhere in the repo (verified by glob over root and `docs/`), so the discovery understanding below was generated from the source itself: entrypoint (`main.py`), ORM models, all eight routers, the Alembic migration, `detector.py`, and the full frontend (`app.js`, `app.html`, `project_details.js`, `dashboard.html`, `sync.js`). Every claim cites the file/line verified. Statements marked "inferred" were not directly traced.*

---

## 1. Executive Summary

The codebase is a competent **single-annotator annotation MVP**, not yet a **multi-user annotation platform**. The annotation canvas itself (box, polygon, SAM magic-wand, YOLO auto-detect, CLIP auto-tag) is real and wired end-to-end, and the AI-assist stack is genuinely local and functional — that is the strongest asset. However, the entire team dimension of the spec is missing: there are no roles, no per-project membership, no review workflow (task statuses are only `New → In Progress → Completed`), no audit trail, and no task locking beyond a fragile optimistic-concurrency check that both the backend and frontend can silently defeat. The data model collapses the spec's Image/Task/Annotation hierarchy into a single `tasks` row with annotations stored as an opaque JSON text blob, which blocks per-annotation auditing, versioning, class usage counts, filtered exports, and QA metrics all at once. Import/export is client-side-only and only round-trips within the tool itself: the "COCO" export is close to schema-valid but the importers dump every annotation onto the *currently open image*, and YOLO/VOC/ZIP formats don't exist at all. **The single biggest risk** is silent data loss around saves: autosave failures only log to the console, a 409 conflict permanently disables autosave for the task, and AI re-detection deletes previously auto-detected (possibly hand-corrected) annotations without confirmation — compounded by AI suggestions committing directly to the database with no accept/reject step (a hard violation of spec §7.1.2). **The single biggest missing capability** is the role-based review workflow (§3, §5.3, §9.4), without which a 15–20 person team cannot operate at all. Incremental remediation is the right path — the FastAPI/SQLite/vanilla-JS skeleton is sound and the repo's own `docs/ARCHITECTURE.md` already charts several of the needed refactors — but the annotation-blob → annotation-rows migration must land early because most other spec items stack on top of it.

---

## 2. Architecture Snapshot (what exists today)

One FastAPI process serves the API, the static frontend, and uploaded images; one SQLite file holds all state; ML inference runs on threads inside the same process. There is no Docker, no WebSocket, no external queue, and no test suite.

- **Backend:** FastAPI, routers mounted in `main.py:46-53`; SQLAlchemy 2 ORM (`models.py`), SQLite in WAL mode with 15s busy timeout (`database.py:12-22`); `Base.metadata.create_all` on startup (`main.py:15`) *and* Alembic with exactly one migration (`alembic/versions/97472310f3a2_initial_migration.py`) — split-brain schema management (also flagged in `docs/ARCHITECTURE.md` §3.7).
- **Auth:** JWT in an httpOnly cookie, bcrypt hashing (`api/auth.py`). `users` table has only `username`/`hashed_password` (`models.py:43-48`) — **no role column**. Three routers (`tasks.py`, `data.py`, `label_studio.py`) have **no auth dependency at all**.
- **AI:** `detector.py` (785 lines) loads YOLOv8-seg ONNX via OpenCV DNN, YOLO-World, ultralytics SAM / HF SAM2, and CLIP, guarded by module-level locks. Jobs run via FastAPI `BackgroundTasks` writing into an in-process `JOBS = {}` dict polled by the client (`api/routers/detect.py:11, 59-91`) — caps the app at one uvicorn worker (`docs/ARCHITECTURE.md` §4).
- **Frontend:** no framework, no build step. `frontend/app.js` is a 4,522-line monolith (annotation engine + API calls + import/export + timers + project sidebar). `frontend/js/utils.js` (33 lines) is the only extracted module. Legacy `sync.js` monkey-patches `localStorage.setItem` and does a synchronous XHR to the unauthenticated `/api/data` blob store.
- **Deployment:** `render.yaml` (single service) + `DATA_DIR` env indirection (`config.py`). No `docker-compose` (spec §9.1 expects it). No `.env.example`, no documented backup procedure.
- **Repo hygiene:** `.jwt_secret`, `messt.jpg`, `parsed_content*.txt` are tracked in git (verified via `git ls-files`); `requirements.txt` pins `python-multipart` twice, keeps unused `passlib`, and omits `ultralytics`/`transformers` which `detector.py` imports.

---

## 3. Data Model Gap Analysis (spec §4)

Actual schema — six tables, 48 lines total (`models.py`):

| Spec entity | Actual | Gap |
|---|---|---|
| Organization/Instance | — | Not needed for single-instance deploy; acceptable omission. |
| Project | `projects` (id, name, slug, type, status, creator, created_at, assignee) | No archive flag, no workflow config, no settings, no guidelines field. `creator`/`assignee` are free-text strings, not FKs to `users`. |
| Project Members + per-project roles | — | **Missing entirely.** `team_members` (`models.py:32-35`) is just a name + time counter, unlinked to `users`; identity is tracked in *three* unlinked places (`users`, `team_members`, `localStorage['dataset_username']` at `app.js:765`). |
| Class schema (project-scoped, color, hotkey, attributes, active flag) | `labels` (id, name, color) — **global**, not project-scoped (`models.py:37-41`; router `labels.py` has no project filter) | No hotkey, no attributes, no parent/group, no deprecated flag, no soft-delete. One team's rename hits every project. |
| Batch / Dataset folder | — | Missing. |
| Image (separate from Task) | Merged: `tasks.image_path` (`models.py:24`) | Image metadata (width/height/hash) not stored anywhere server-side; duplicate detection impossible. |
| Task (status, assignee, reviewer, history) | `tasks` (status, assignee, time_spent, updated_at) | No reviewer, no history, no priority. Observed status values are only `New` / `In Progress` / `Completed` (`schemas.py:24`, `app.js:767`, `projects.py:28`). |
| **Annotation** (row per object; class FK, attributes, tags, z-order, created_by/updated_by, version) | **A JSON text blob**: `tasks.annotations = Column(Text)` (`models.py:30`) | The central structural gap. No per-annotation identity server-side → no audit trail (§9.4), no versioning, no class usage counts (§5.4), no per-class export filters (§8.2), no QA metrics (§5.5), no acceptance-rate tracking (§7.1.7). The blob is written wholesale on every autosave (`tasks.py:67-68`). |
| Image-level Tags | — | Missing. The CLIP "auto-tag" flow ends by creating *class-list entries*, not image tags (`app.js:602-609` → `ensureLabel`). |
| ImportJob / ExportJob | — | Missing (no import/export exists server-side at all). |
| Audit fields / soft-delete | `created_at` on projects/users only | No `created_by`/`updated_by` anywhere; all deletes are hard deletes (`tasks.py:93-105`, `projects.py:111-119`). |

Also structural: `workspace_data` (`models.py:4-7`) is a single shared key/value blob mirrored from `localStorage` by `sync.js` for **all users, last-writer-wins, unauthenticated** — a parallel persistence path that overlaps the real tables (`docs/ARCHITECTURE.md` §3.6).

---

## 4. Feature Coverage Matrix

Status legend: ✅ Implemented · 🟡 Partially Implemented · ❌ Missing · ⚠️ Implemented but fragile

### §3 Roles & Permissions

| Requirement | Status | Evidence | Gap |
|---|---|---|---|
| Auth (JWT/session, bcrypt) | ⚠️ | `api/auth.py:47-61`, cookie set in `api/routers/auth.py:39-45` | Works, but `.jwt_secret` is committed to git (secret compromised — `git ls-files`, `docs/GOTCHAS.md` #1), and `tasks`/`data`/`label_studio` routers skip auth entirely (`tasks.py:12` vs `projects.py:14`) — anyone on the network can read/modify/delete every task. |
| 4 roles (Admin/PM/Reviewer/Annotator) | ❌ | `models.py:43-48` — `User` has no role field; no permission check anywhere in `api/` | Every logged-in user can do everything, including delete whole projects (`projects.py:111`). |
| Per-project membership/roles | ❌ | No membership table in `models.py`; no filter in `tasks.py:14-45` | Any user loads any project's tasks. |
| Audit log | ❌ | No audit table; no `created_by` on annotations (blob items carry no author — `app.js:688-695`) | "Who labeled this and when" is unanswerable. |

### §5.1 Project Management

| Requirement | Status | Evidence | Gap |
|---|---|---|---|
| Create/edit/delete project | 🟡 | `projects.py:88-119` | Create/rename/status/assignee/delete exist. No archive; delete is destructive cascade. Legacy `POST /api/projects/update` verb style. |
| Project type (Box/Polygon/Both) | 🟡 | `projects.py:90` stores `type`; default `"Image - Polygon"` (`schemas.py:11`) | Stored but never enforced — the editor always offers all tools regardless of type (no reference to project type in `app.js`). |
| Class schema per project, bulk class import | 🟡 | Classes are global (`labels.py`); JSON class import/export exists client-side (`app.js:2698-2787`) | No project scoping, no CSV import, no reorder. |
| Member/role assignment per project | ❌ | — | See §3. |
| Workflow config (1-stage vs 2-stage) | ❌ | — | No review stage exists at all. |
| Project settings (polygon limits, required attrs, guidelines panel) | ❌ | — | None. |

### §5.2 Dataset / Image Management

| Requirement | Status | Evidence | Gap |
|---|---|---|---|
| Bulk upload (drag-drop, folder, ZIP) | 🟡 | Multi-file upload → one task per file (`projects.py:123-150`); synchronous, reads whole file into memory (`projects.py:143`) | No ZIP, no folder recursion, no drag-drop on project page; blocks the request thread for large batches. Allowed extensions (`.png .jpg .jpeg .gif .webp`, `projects.py:129`) diverge from spec (BMP/TIFF missing, GIF extra). |
| Image list: grid + table, filters | 🟡 | Table with search/status filter/sort/pagination in `project_details.js:107-153` | Client-side only over a full fetch; no thumbnail grid; no filter by class/tag/date/batch. |
| Search by filename/tag/class/count | 🟡 | Filename substring only (`project_details.js:113`) | No tag/class/count search (impossible against the blob). |
| Batch ops (assign, status, delete, tag, move) | 🟡 | Bulk delete + bulk assign endpoints (`tasks.py:99-123`) wired to UI (`project_details.js:356-399`); bulk status endpoint exists but has no UI control | No bulk tag / move-to-batch (no batches). |
| Pagination for 10k+ images | ⚠️ | `GET /api/tasks` returns **every task incl. full annotation blobs** (`tasks.py:14-45`); workspace loads all of them into `state.gallery` (`app.js:4337-4352`) | Will degrade badly at spec scale (§9.3); `include_annotations=false` exists but the workspace doesn't use it. |
| Duplicate detection (hash) | ❌ | Files renamed to `uuid4` on upload (`projects.py:136`) — original bytes never hashed | None. |

### §5.3 Task Management

| Requirement | Status | Evidence | Gap |
|---|---|---|---|
| Status lifecycle incl. Submitted/In Review/Approved/Rejected | ❌ | Only `New`/`In Progress`/`Completed` observed (`app.js:766-767`, `projects.py:28,44-49`) | No review states, no transitions UI, nothing enforces the funnel. |
| Manual + auto assignment | 🟡 | Manual per-task (edit modal `project_details.js:434-457`) + bulk assign (via `prompt()`); upload can preset assignee (`projects.py:124`) | No round-robin/balanced auto-distribution; assignee is free text, not a validated user. |
| "My Queue" for annotators | ❌ | `loadWorkspaceTasks` fetches the whole project unfiltered (`app.js:4340`) | Every annotator sees and can edit everything; autosave even overwrites `assignee` to whoever opened it (`app.js:778`). |
| Reviewer queue + diff | ❌ | — | No review concept. |
| Comments per task | 🟡 | Comments exist as canvas annotation objects `{type:"comment"}` (`app.js:2444+`, counted in `projects.py:33-38`) | Pin-on-canvas notes, not a threaded reviewer↔annotator conversation; no read/resolved state. |
| Task locking | ⚠️ | Optimistic check: client sends `updated_at`, server 409s if row is >1s newer (`tasks.py:52-57`) | No visible lock/presence. Two fragilities: unparseable timestamp → check silently skipped and the save proceeds (`tasks.py:57-58`, GOTCHAS #12); on 409 the frontend alerts once then sets `currentTask.id = null`, **silently disabling all future autosaves for that task** (`app.js:785-789`) — the user keeps annotating into a void. |

### §5.4 Class Management

| Requirement | Status | Evidence | Gap |
|---|---|---|---|
| Per-project class list + usage counts | 🟡 | Global class CRUD UI (workspace panel `app.js:1341-1470`; dashboard modal `dashboard.html:386-539`); per-image counts only (`app.js:1750-1764`) | Not project-scoped; no project-wide usage counts (blob prevents cheap counting). |
| Rename-with-propagation, merge, deactivate | 🟡 | Rename propagates because annotations reference `labelId` (`app.js:2108-2112`); delete cascades to annotations after confirm (`dashboard.html:515-527`) | No merge; no deactivate — only destructive delete. |
| Color + hotkey editable | 🟡 | Color yes (`dashboard.html:489`); hotkey — no concept anywhere | No class hotkeys at all. |
| Attribute schema editor | ❌ | `buildExportAnnotation` hardcodes `attributes: []` (`app.js:1966`) | No attributes anywhere in the stack. |

### §5.5 Dashboards

| Requirement | Status | Evidence | Gap |
|---|---|---|---|
| Project dashboard (funnel, class distribution, burndown, time-per-task) | 🟡 | Progress % + comment counts (`projects.py:24-51`, batch `53-85`); dashboard page lists counts in modals (`dashboard.html:380-565`) | No charts, no by-status funnel beyond completed-count, no burndown, no per-class distribution. Dashboard reads the legacy shared `workspace` blob, so it reflects one shared workspace, not per-project truth. **Bug:** the metrics GET writes `project.status` and commits (`projects.py:42-49` — GOTCHAS #6). |
| Team/annotator dashboard | 🟡 | Only total time logged per member (`team.py:13-16`, `dashboard.html:541-557`) | No throughput, no pass/reject rate (no review), no averages. |
| QA dashboard | ❌ | — | No review data exists to display. |
| Dashboard CSV export | ❌ | — | None. |

### §6 Annotation Workspace

| Requirement | Status | Evidence | Gap |
|---|---|---|---|
| Pan/zoom | ✅ | Wheel-zoom around cursor (`app.js:3118-3125`), right-drag pan (`app.js:3128-3139, 3374-3378`) | No zoom-to-fit/zoom-to-selection commands or keyboard zoom. |
| High-res rendering performance | ✅ | Three stacked canvases (image/static/dynamic) (`app.js:23-28`, `drawAllLayers` 926), canvas-only drawing — no DOM-per-point | Reasonable design; untested at "hundreds of objects" but architecture is right. |
| Keyboard next/prev image | ❌ | Prev/next are buttons only (`app.js:2368-2369`); keydown handlers cover only Delete/Backspace/Escape/Enter (`app.js:3621, 3661`) | No `A`/`D`/arrow navigation. |
| Minimap / filmstrip | ❌ | Only a "3 / 12" counter (`app.js:2357-2362`) | None. |
| Brightness/contrast | ❌ | No filter code in `app.js` (grep: no matches) | None. |
| Box: draw/resize/move/delete | ✅ | Drag-draw; vertex handles (`drawVertexHandles` 1172); hit-test move; Del/Backspace delete (`app.js:3621`) | Boxes are stored as 4-point polygons — fine, but resize is per-corner (no edge handles). |
| Box: snap, copy/paste, arrow nudge, numeric input | ❌ | No clipboard/nudge/snap code (grep: no matches) | All missing — the spec's "pixel-perfect correction" tools don't exist. |
| On-draw class picker + class hotkeys | 🟡 | Draw applies the currently active class from the side panel (`app.js:687`) | No searchable dropdown on draw, no hotkey switching — heavy mouse dependence. |
| Polygon: click-to-build, close, vertex edit, insert on edge | ✅ | `finalizePolygon` (3073) with dblclick close (3474); vertex drag + midpoint insertion via `hitTestLine` (3156, 3192, 3405) | Solid core. |
| Multi-part polygons / holes | 🟡 | Multi-part via annotation grouping → exported as multi-segment COCO annotation (`groupSelectedAnnotations` 2546, export `app.js:1841-1886`) | No holes (no even-odd rendering or negative parts). |
| Magnetic edge snap / simplify / self-intersection check | 🟡 | Simplify exists only for SAM output via precision slider (`app.js:647-649`) | No manual-polygon simplify, no edge snapping, no validation. |
| Layer/z-order panel, hide/lock objects | ❌ | Object list has select/delete only (`renderAnnotations` 1472+) | No hide/show/lock/reorder. |
| Object list + attribute panel | 🟡 | Object list panel yes (`app.html` #annotationList, `app.js:1472`) | No attributes to edit. |
| Undo/redo | 🟡 | Undo with 50-step history (`snapshot` 745, `app.js:2502-2510`) | **No redo** (no redo stack in `app.js`). |
| Autosave + visible saved state | ⚠️ | Debounced 1s save → localStorage + `/api/tasks` (`app.js:801-817`), `#saveStatus` indicator, flush on tab-hide (`app.js:830-838`) | Failure path violates §9.4: network errors only `console.error` (`app.js:798`) while the UI already said "Saved" (`app.js:808` fires before the request); 409 disables saving permanently (see §5.3 row). |
| Shortcut cheat-sheet | ❌ | — | None (and few shortcuts to document). |
| Image-level tag input | ❌ | "Classes in current image" panel is derived from object annotations (`app.js:1710-1771`), not free tags | No image-level tags. |
| "Mark as empty" | ❌ | — | Empty images sit as `New`/`In Progress` forever. |

### §7 AI-Assist — see section 6 below for the architecture verdict

| Requirement | Status | Evidence | Gap |
|---|---|---|---|
| Pre-labeling (detector + SAM) | ✅ | YOLOv8-seg boxes+mask-polygons (`detector.py:294-402`), YOLO-World text prompts (`detector.py:494-521`), full UI wiring (`app.js:336-438`) | Exists and works per-image. |
| Suggestions never auto-commit (§7.1.2, hard req) | ❌ **data-quality risk** | Detections are converted and pushed straight into `state.annotations` then `save()`d to the DB (`app.js:407-424`, `predictionsToAnnotations` 714-743); same for magic-wand (`app.js:679-703`) | No suggested state, no dashed styling (the only `setLineDash` is the in-progress polygon preview, `app.js:1002`), no accept/reject. Worse: re-running detect in replace mode deletes all prior `source:"auto-detect"` annotations (`app.js:393-418`) — including ones the annotator has since hand-corrected (edits don't clear `source`). Only CLIP auto-tag uses a confirm modal (`app.js:499-618`). |
| Interactive click-to-segment | ✅ | SAM point prompts with shift=add / alt=subtract refinement (`app.js:620-712`), MobileSAM/SAM2/HF-SAM2 selectable (`detector.py:621-784`) | Best-implemented AI feature; keep. |
| Class/tag suggestion as chips | 🟡 | CLIP top-5 chips with scores + confirm modal (`app.js:568-611`) | "Apply" only creates class-list entries (`ensureLabel`, `app.js:606`) — tags aren't attached to the image, so the output goes nowhere useful. |
| Batch pre-annotation jobs | ❌ | Detection is invoked per-open-image from the browser only (`app.js:336`) | No project-level job, no progress tracking. |
| Model management per project | 🟡 | Model/size/confidence chosen from per-browser localStorage (`app.js:371-373, 662`) + env vars (`detector.py:17-31`) | Per-user, not per-project; no version registry. |
| Acceptance/edit-rate metric | ❌ | `score`/`source` are stored on annotations (`app.js:737-738`) but nothing aggregates them | No tracking. |
| Confidence display | 🟡 | Scores shown in auto-tag modal (`app.js:576`); stored on detected annotations | Not rendered on canvas/object list; can't prioritize low-confidence review. |

### §8 Import/Export — see section 5 below for verification detail

| Requirement | Status | Evidence |
|---|---|---|
| COCO export | 🟡 | Client-side single-JSON download (`buildCocoExport` 1807-1891) |
| COCO import | 🟡 | Objects-panel importer (`app.js:2799-2907`) — current image only |
| YOLO export/import | ❌ | No code (grep for YOLO-format writers: none) |
| CSV export/import | 🟡 | Custom columns, tool-internal only (`app.js:2033-2082`, `2200-2307`) |
| ZIP bundle either direction | ❌ | No archive code anywhere |
| Pascal VOC | ❌ | No code |
| Export filters (status/class/date/tag/batch) | ❌ | Exports always dump the whole loaded gallery (`app.js:1825-1830`) |
| Async import/export jobs + history | ❌ | Everything is synchronous in the browser; no server endpoints |
| Programmatic/SDK export endpoint | ❌ | Only outbound push of the current payload to Label Studio (`api/routers/label_studio.py`) or an arbitrary URL (`app.js:1893-1932`) — not an export API |

### §9 Non-Functional

| Requirement | Status | Evidence | Gap |
|---|---|---|---|
| docker-compose local deploy | ❌ | Only `render.yaml`; no Dockerfile/compose (glob verified) | Runs fine as bare uvicorn; compose absent. |
| Local models, no SaaS dependency | ✅ | All models open-weights, auto-downloaded to `DATA_DIR/models` (`detector.py:91-141`) | Label Studio push is optional. Spec met. |
| `.env` config, persistent volume, backup docs | 🟡 | Env vars for DATA_DIR/JWT/model knobs (`config.py`, `api/auth.py:14`, `detector.py:17-31`) | No `.env.example`; no backup procedure documented. |
| Async endpoints, Pydantic response models | 🟡 | Pydantic on requests; `response_model` only on `labels.py:12` and auth | All routers are sync `def` (fine for SQLite but uploads/exports should be async); responses are hand-built dicts (GOTCHAS #13). |
| Background jobs for long work | 🟡 | Only AI inference (`detect.py`); uploads are blocking; no import/export jobs | `JOBS` dict leaks unpolled results and dies at >1 worker (`ARCHITECTURE.md` §3.4). |
| WebSocket/polling for locks + job progress | 🟡 | Polling exists for detect jobs only (`detect.py:59-70`) | No lock/status live updates. |
| PostgreSQL + migrations | 🟡 | SQLite WAL + Alembic-in-name-only (see §2) | Deviation defensible at this team size — see "spec deviations worth keeping". |
| 10k images w/o degradation | ❌ | Full-table fetches (see §5.2 row) | Needs server-side pagination. |
| Hundreds of objects w/o lag | ✅ (inferred) | Canvas pipeline, no per-point DOM | Not load-tested. |
| 15–20 concurrent editors, no data loss | ⚠️ | Optimistic 409 + WAL only | See locking + autosave rows; currently unsafe. |
| No silent data loss (§9.4) | ❌ | `app.js:798, 785-789`; `tasks.py:57-58` | Multiple silent-loss paths, detailed above. |
| Audit trail | ❌ | — | Blocked by blob model. |
| Keyboard-first UX | ❌ | 4 keys total | See §6. |

---

## 5. Import/Export Verification Results

Traced round-trips statically (no format claimed by file names was trusted):

1. **COCO export** (`buildCocoExport`, `app.js:1807-1891`): structurally valid `images`/`annotations`/`categories`; polygons and boxes both emitted as `segmentation` arrays with derived `bbox`; grouped annotations correctly become multi-polygon `segmentation`. Deviations: `area` is bbox area, not polygon area; nonstandard `num_objects` key; **`file_name` is the original upload name but `width`/`height` export as `0` for any image not opened during the session** (gallery entries init to 0 at `app.js:2319-2321`/`4348-4349` and are only populated on image load, `app.js:1781-1784`) — this corrupts downstream YOLO-style normalization for consumers. Also exports the *whole gallery from browser memory*, so it silently reflects only what the browser loaded.
2. **COCO import** (`app.js:2809-2851`): parses `categories` → labels and `segmentation`/`bbox` → objects correctly, **but ignores `images` and `image_id` entirely and appends every annotation to the currently open image.** A 500-image COCO file imports as 500 images' worth of boxes stacked on one photo. Export→import round-trip only survives for a single-image dataset.
3. **Generic JSON import** (`importData`, `app.js:2084-2198`): same single-image collapse (all tasks' annotations merged onto the current canvas, `app.js:2094-2101`); does apply width/height rescaling per source task. It cannot read the tool's own COCO export (expects `labels`/`annotations` keys or a task array, not `categories`).
4. **CSV** (`app.js:2033-2082` out, `2200-2307` in): custom schema (`image,label,type,x,y,w,h,imgWidth,imgHeight,points-as-escaped-JSON`) — not any standard ML CSV; round-trips within the tool only (inferred from matching column handling; not executed). Comma-containing labels would break the naive `join(",")` writer (no quoting of label field, `app.js:2060`).
5. **YOLO / Pascal VOC / ZIP:** no producing or consuming code exists anywhere in the repo (grep across `*.py`/`*.js`).
6. **"FastLabel-style" task export** (`buildExportTasks`, `app.js:1974-2008`): defined but **never called** — dead code (only reference is its definition).
7. **Server-side:** zero import/export endpoints; `label_studio.py` pushes the current annotation payload to an external Label Studio instance (and swallows the real error on failure, `label_studio.py:46-47`).

**Net:** of the spec's 5 formats × 2 directions, only "COCO-ish export" and "CSV within this tool" partially exist; nothing is job-based, filtered, or server-side.

---

## 6. AI-Assist Architecture Findings

- **Capability exists and is real** — the strongest part of the codebase relative to spec §7: promptable detection (YOLO-World), seg-mask polygons (YOLOv8-seg), iterative SAM point-prompt segmentation with add/subtract refinement, CLIP classification, GPU-if-available (`detector.py` throughout).
- **Decoupling is half-done (§7.1.6 / §9.2).** Inference does not block the HTTP request (job-id + polling, `detect.py:72-91`), but it runs on `BackgroundTasks` threads *inside the API process*: a 30s SAM call competes with request handling for CPU/GIL, the `JOBS` dict leaks results whose client never polls (closed tab), and any move to >1 uvicorn worker breaks polling with spurious 404s (`ARCHITECTURE.md` §3.4). The spec's "separate inference service/process" is not met.
- **Suggestions auto-commit — flagged as a data-quality risk, not just a gap (per §7.1.2).** Detection results are inserted as ordinary annotations and persisted to SQLite within ~1s via autosave (`app.js:407-424` → `save()` → `syncToBackend`). There is no visual distinction (dashed outline etc.), no accept/edit/reject flow, and no way to tell reviewed truth from raw model output afterward — `source:"auto-detect"` survives on the row but nothing consumes it. Compounding risk: re-running Auto-Detect in default replace mode deletes all existing `auto-detect` annotations first (`app.js:393-418`); since manual edits don't clear `source`, an annotator who spent 20 minutes correcting model boxes loses that work by clicking the button again.
- **Batch pre-annotation (§7.1.5): absent.** Inference is only triggered from an open canvas, one image at a time, with the browser as orchestrator.
- **Model selection (§7.1.6): per-browser localStorage** (`ai_model_size`, `ai_sam_model`, `ai_conf` — `app.js:371-373, 662`), not per-project config; two annotators on one project can silently use different models/thresholds.
- **Metrics (§7.1.7/§7.1.8): absent** beyond storing `score` on each detected annotation; nothing surfaces or aggregates it.

---

## 7. Risk-Classified Gap List

### A. Data-integrity / architecture risks
1. **Silent autosave failure** — UI shows "Saved" before the request fires; network failure only hits the console (`app.js:798, 808`). Violates §9.4 directly.
2. **409 conflict permanently disables autosave** for the open task (`app.js:788`) — user keeps working, nothing persists.
3. **Optimistic-lock check silently skipped** on unparseable client timestamp (`tasks.py:57-58`) — overwrites a teammate's work exactly when timestamps are already wrong.
4. **AI suggestions auto-commit + replace-mode deletion of corrected work** (`app.js:393-424`) — poisons label quality and destroys human effort (§7.1.2 hard requirement).
5. **Unauthenticated `tasks`/`data`/`label_studio` routers** (`tasks.py:12`) + **committed `.jwt_secret`** — full read/write/delete of all annotation data without login; token forgery possible for anyone with repo access.
6. **Annotations-as-blob schema** (`models.py:30`) — precludes audit, versioning, filtered export, metrics; every save rewrites all annotations of a task (large blast radius for bugs).
7. **Shared `workspace_data` blob via monkey-patched localStorage** (`sync.js`, `data.py`) — cross-user last-writer-wins overwrite channel, unauthenticated.
8. **Split-brain schema management** (`main.py:15` + Alembic) — future column additions will silently not reach existing databases.
9. **`JOBS` in-process queue** — memory leak for unpolled jobs; hard single-worker cap (`detect.py:11`, `ARCHITECTURE.md` §3.4).
10. **COCO export emits `width/height: 0`** for unopened images (`app.js:1836-1837`) — corrupt training data downstream.
11. **GET endpoint writes to DB** (`projects.py:42-49`) — status changes as a side effect of viewing metrics.

### B. Core-workflow blockers
12. No roles/permissions/per-project membership (§3).
13. No review workflow — statuses, reviewer queue, approve/reject/rework loop (§5.3, §9.4).
14. No "My Queue"/assignee scoping; opening a task reassigns it to the opener (`app.js:778`).
15. No task locking/presence indication for concurrent annotators (§5.3, §9.3).
16. No server-side pagination/filtering — full-project fetches with blobs (§5.2, §9.3).
17. Import/export: no YOLO/VOC/ZIP, no filters, no jobs, importers collapse multi-image datasets onto one image (§8).
18. Class schema not project-scoped; no attributes; no hotkeys; destructive-only class deletion (§5.4, §4).
19. Keyboard-first editor gaps: no redo, no copy/paste/nudge, no class hotkeys, no keyboard image nav (§6).
20. Blocking, memory-buffered bulk upload; no ZIP/folder ingestion (§5.2).

### C. AI-assist / reporting gaps
21. Suggested-annotation review UX (accept-all/individually) — the fix for risk #4 above.
22. Batch pre-annotation as an async job with progress (§7.1.5).
23. Acceptance/edit-rate + confidence surfacing (§7.1.7-8).
24. Per-project model configuration (§7.1.6).
25. Dashboards: status funnel, per-annotator throughput, QA metrics, CSV export (§5.5).
26. Image-level tags (real ones) + CLIP tag suggestions attaching to the image (§4, §7.1.4).

### D. Polish / nice-to-have
27. Minimap/filmstrip, brightness/contrast, zoom-to-fit (§6.1).
28. Polygon holes, simplify for manual polygons, self-intersection validation, snap modes (§6.3).
29. Layer panel: hide/lock/z-order (§6.4); "mark as empty"; shortcut cheat-sheet (§6.4).
30. `app.js` decomposition into `frontend/js/` modules (repo's own priority, `ARCHITECTURE.md` §3.1).
31. Repo hygiene: debris files, `requirements.txt` duplicates/missing deps, `test_*.py` misnomers, `docker-compose`, `.env.example`, backup docs, tests scaffold.

---

## 8. Prioritized Remediation Plan

Incremental remediation throughout — no rewrite is needed; the FastAPI/SQLite/canvas core is sound and several fixes are already charted in the repo's own docs. Ordered by the spec's mandated priority (data-integrity → workflow → AI/reporting → polish). Effort: S ≤ 1 day, M = 2–5 days, L = 1–3 weeks.

### Phase 1 — Stop the bleeding (all S, no dependencies, ship immediately)
| # | Change | Why (spec) | Effort |
|---|---|---|---|
| 1.1 | Rotate `.jwt_secret` (`git rm --cached`, gitignore, delete local copy); purge `messt.jpg`/`parsed_content*` | §3 auth integrity; GOTCHAS #1 | S |
| 1.2 | Add `dependencies=[Depends(get_current_user)]` to `tasks`, `data`, `label_studio` routers | §3; ARCHITECTURE §3.2 | S |
| 1.3 | Autosave truthfulness: set "Saved" only on 2xx; on failure show a persistent error banner and retry with backoff; on 409 offer "reload their version / keep mine" instead of `currentTask.id = null` | §9.4 no-silent-loss | S |
| 1.4 | Reject unparseable `updated_at` with 400 (`tasks.py`); replace `datetime.utcnow()` with aware UTC | §9.3 concurrency; GOTCHAS #7/#12 | S |
| 1.5 | Move metrics status-write out of the GET (do it in the task-update path) | GOTCHAS #6 | S |
| 1.6 | Fix COCO export zero-dims: store image width/height server-side at upload (PIL already available) and return them with tasks | §8.2 correctness | S |
| 1.7 | `requirements.txt` cleanup (dedupe `python-multipart`, drop `passlib`, add `ultralytics`, `transformers` with pins) | §9.1 reproducibility | S |

### Phase 2 — Data model foundation (sequencing anchor for everything below)
| # | Change | Why | Effort | Depends on |
|---|---|---|---|---|
| 2.1 | **Normalize annotations into an `annotations` table** (id, task_id FK, class FK, geometry JSON, source, score, created_by, created_at, updated_by, updated_at, deleted_at). Alembic migration backfills from the blob; keep blob read-fallback for one release. Batch-write endpoint to keep autosave cheap. | §4 core model; unblocks audit (§9.4), filtered export (§8.2), metrics (§5.5, §7.1.7) | L | 1.4 |
| 2.2 | Make Alembic the only schema path: run `alembic upgrade head` on startup; keep `create_all` for empty-DB bootstrap only | §9.2 | S | — |
| 2.3 | Users/roles: add `role` to `users`, add `project_members` (user, project, role); replace free-text `assignee`/`creator` with user FKs; merge `team_members` time tracking into users | §3, §4 | M | 2.2 |
| 2.4 | Project-scope the class table (`project_id` FK, `hotkey`, `is_active`, ordering); add `class_attributes` table (name, type, required, options) | §4, §5.4 | M | 2.2 |
| 2.5 | Add `images` metadata columns (or table): original filename, sha256 (dedupe base), width/height, batch FK; add `batches` table | §4, §5.2 | M | 2.2 |
| 2.6 | Retire `sync.js` + `/api/data`: move the workspace-blob reads (dashboard) onto real endpoints, then delete both | Risk #7; ARCHITECTURE §3.6 | M | — |

### Phase 3 — Team workflow (the biggest missing capability)
| # | Change | Why | Effort | Depends on |
|---|---|---|---|---|
| 3.1 | Task state machine: `Not Started → In Progress → Submitted → In Review → Approved / Rejected(→In Progress)`; server-side transition validation; migrate `New`/`Completed` values | §5.3, §9.4 | M | 2.3 |
| 3.2 | Permission enforcement per role/route (annotators: own tasks only; PM: own projects; reviewer: review queue) + "My Queue" and reviewer-queue endpoints with server-side filters; stop autosave from mutating `assignee` | §3, §5.3 | M | 2.3, 3.1 |
| 3.3 | Task locking: soft lock (locked_by, heartbeat, TTL) surfaced in list + editor banner; polling endpoint (WebSocket later if needed) | §5.3, §9.3 | M | 3.2 |
| 3.4 | Threaded task comments (table + panel) wired into reject/rework loop; keep canvas pin-comments as anchors | §5.3, §9.4 | M | 3.1 |
| 3.5 | Server-side pagination/filtering/sorting for `/api/tasks` (status, assignee, class, batch, date) + thumbnail grid view; workspace filmstrip loads pages lazily | §5.2, §9.3 | M | 2.1, 2.5 |
| 3.6 | Upload hardening: async/streamed saves, ZIP ingestion with extraction, duplicate detection via sha256, auto-distribution (round-robin) option | §5.2, §5.3 | M | 2.5 |
| 3.7 | Audit log (who/what/when on annotation + task + class mutations), simple append-only table | §3, §9.4 | S/M | 2.1, 2.3 |

### Phase 4 — AI-assist to spec
| # | Change | Why | Effort | Depends on |
|---|---|---|---|---|
| 4.1 | **Suggestion workflow**: detections land as `status='suggested'` annotations (dashed render, confidence chip); accept-all / accept-one / edit-then-accept / reject; replace-mode only ever touches still-suggested items | §7.1.1-2 (hard req); fixes risk #4 | M | 2.1 |
| 4.2 | Harden job queue: TTL sweep for `JOBS`, then move job state into SQLite (same polling API) to lift the 1-worker cap; optionally split inference into a second process later | §7.1.6, §9.2 | M | — |
| 4.3 | Batch pre-annotation endpoint ("run on all Not Started in project") as a tracked job with progress + cancel | §7.1.5 | M | 4.1, 4.2 |
| 4.4 | Per-project AI config (model, confidence, SAM variant) stored on the project; localStorage becomes per-user override only | §7.1.6 | S | 2.2 |
| 4.5 | Acceptance/edit-rate metrics from suggestion outcomes + confidence display in object list | §7.1.7-8 | M | 4.1, 3.7 |
| 4.6 | Real image-level tags (table + input field) and route CLIP suggestions there as chips | §4, §7.1.4 | S/M | 2.1 |

### Phase 5 — Import/export overhaul
| # | Change | Why | Effort | Depends on |
|---|---|---|---|---|
| 5.1 | Server-side export service: COCO (fix area, drop nonstandard keys), YOLO (box + seg, normalized, `data.yaml`), CSV, VOC XML, ZIP bundle with manifest; filters by status/class/batch/date/tag; async `export_jobs` rows with artifact download + history; programmatic endpoint | §8.2 | L | 2.1, 2.4, 3.1 |
| 5.2 | Server-side import service: COCO/YOLO/CSV/ZIP with **per-image routing by filename**, pre-import summary (images/annotations/classes matched), class-mapping step, async `import_jobs` with error report | §8.1; fixes the single-image collapse | L | 2.1, 2.5 |
| 5.3 | Delete dead `buildExportTasks`; move remaining client exporters behind the server API | hygiene | S | 5.1 |

### Phase 6 — Editor & polish (parallelizable, mostly independent)
| # | Change | Effort |
|---|---|---|
| 6.1 | Keyboard-first pass: redo stack, class hotkeys (1-9), A/D image nav, copy/paste box, arrow nudge, zoom-to-fit, shortcut overlay | M |
| 6.2 | Object list upgrades: hide/show, lock, z-order; "mark as empty" action (feeds task status) | M |
| 6.3 | Polygon extras: holes (even-odd fill + subtract mode), simplify action, self-intersection warning; brightness/contrast sliders; filmstrip | M/L |
| 6.4 | Dashboards: status funnel, per-annotator throughput, QA/rejection breakdown, CSV export (cheap once 2.1/3.1/3.7 exist) | M |
| 6.5 | `app.js` decomposition into `frontend/js/` modules along `ARCHITECTURE.md` §3.1 seams — do it opportunistically as each phase touches a seam (suggestion layer → `aiAssist.js`, exports → gone server-side, etc.) | ongoing |
| 6.6 | `docker-compose.yml` (app + volume; optional separate inference service from 4.2), `.env.example`, documented backup procedure (SQLite backup API + uploads rsync), pytest scaffold in `tests/` with the Phase 1–3 endpoints covered | M |

**Spec deviations worth keeping** (honest comparison, not checklist completion):
- **SQLite (WAL) instead of PostgreSQL** (§9.2): defensible for 15–20 users on one box, already tuned (`database.py`), and swapping adds ops burden for everyone (`ARCHITECTURE.md` §4). Keep, but treat the SQLAlchemy layer as the escape hatch; revisit only if lock contention is measured after Phase 3.
- **No frontend build step / framework**: deliberate (`ARCHITECTURE.md` §4) and workable if 6.5 proceeds; the spec doesn't mandate a framework.
- **Polling instead of WebSocket** for job/lock status: explicitly allowed by §9.2; keep polling until lock UX proves it insufficient.
- **Optimistic concurrency on saves** already matches §9.3's "optimistic concurrency on annotation saves" — fix its two bypasses (Phase 1) rather than replacing it with pessimistic locks.
- **SAM point-refinement UX** (shift-add/alt-subtract, model choice incl. SAM2) is ahead of the spec's baseline — preserve it through the suggestion-workflow change.

---

## 9. Open Questions (need a human answer)

1. **Existing production data:** is there a live `workspace.db` with real annotation volume on Render? The Phase 2 blob→rows migration plan (and how carefully to backfill/stage it) depends on this. Also: are current annotations trusted, given that model outputs were auto-committed indistinguishably from human work (`source` field aside)?
2. **The committed `.jwt_secret`:** has the repo ever been shared/pushed anywhere non-private? Determines urgency of forcing re-login vs. also auditing for tampered data on the deployed instance.
3. **Hardware for AI-assist:** does the target deployment box have a GPU? Batch pre-annotation (4.3) sizing and whether a separate inference process (4.2 option) is worth it depend on this.
4. **Team reality:** actual number of annotators today, and is the two-stage review flow (§5.1) required from day one, or is single-stage acceptable while Phase 3 lands incrementally?
5. **Label Studio integration** (`api/routers/label_studio.py`): still used by anyone, or removable? It's unauthenticated and pushes data to an external service; if unused, deleting it shrinks the attack surface in Phase 1.
6. **Render vs. on-prem:** the spec says on-prem docker-compose; the repo targets Render (`render.yaml`). Which is the actual deployment target going forward? (Affects 6.6 and the backup story.)
7. **Image formats:** spec lists BMP/TIFF/WebP; upload currently allows GIF but not BMP/TIFF (`projects.py:129`). Confirm the real needed set before changing validation.
8. **Dataset scale:** expected images per project (spec says tens of thousands) — determines how aggressive 3.5 pagination/virtualization needs to be and whether SQLite reconsideration moves up.
