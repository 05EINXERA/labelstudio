# Application Requirements — Local Image Annotation Platform

## Document Purpose
This document defines the required scope, feature set, UX expectations, and technical shape of an internal, locally-deployed **Image Annotation Application** for a team of 15–20 annotators. It is written to be used as an **input spec for a discovery/gap-analysis pass** against an existing codebase (via `discovery.md` or direct code analysis). The output of that comparison should be a refactoring / architecture / UI-UX / feature-completion plan.

It is grounded in the feature patterns of **FastLabel** (fastlabel.ai) and comparable annotation platforms (V7 Darwin, Label Studio, SuperAnnotate, X-AnyLabeling), scoped down to what a 15–20 person in-house team doing **bounding box and polygon** annotation actually needs — i.e. this is not a request to rebuild FastLabel's full enterprise AIPaaS (video, 3D/point cloud, audio, text, outsourcing marketplace, MLOps). Those are explicitly **out of scope**.

---

## 1. Scope Summary

| Dimension | Decision |
|---|---|
| Data modality | Images only (JPEG/PNG/BMP/TIFF/WebP) |
| Annotation geometry types | Bounding Box, Polygon (incl. multi-polygon / holes) |
| Team size | 15–20 concurrent annotators + reviewers + admins |
| Deployment | Self-hosted / local (single-server or docker-compose, on-prem or single-VM), not multi-tenant SaaS |
| Backend | FastAPI (Python), REST + WebSocket |
| Auth | Local user accounts, role-based, no external SaaS dependency required |
| AI-assist | Model-assisted pre-labeling, class/tag suggestion, semi-automated polygon/box generation |
| Primary reference product | FastLabel (project → task → image → annotation hierarchy, class/attribute schema, SDK-style import/export) |

---

## 2. Reference Product Notes: FastLabel Patterns Being Adopted

FastLabel's annotation product (and its published Python SDK) organizes work as:

- **Project** — a container with a defined *project type* (e.g. Image - Bounding Box, Image - Polygon, Image - All), an editable **class/label schema**, and its own members/roles.
- **Task** — one task = one image (or one unit of work) inside a project, with a lifecycle status (e.g. Not Started / In Progress / Completed / Approved / Rejected/Sent Back), an assignee, and a reviewer.
- **Class (Label) schema** — project-scoped list of classes, each with a color, hotkey, and optional **attributes/sub-schema** (e.g. a "car" class can carry an attribute "color: red/blue/white" or "occluded: true/false").
- **Annotation object** — geometry (box or polygon points) + class + optional attributes + optional free-text tag, stored per task/image, versionable.
- **Import/Export pipeline** — dataset-level export jobs producing standard ML formats (COCO JSON, YOLO txt, Pascal VOC XML, CSV) as downloadable archives, plus SDK/API access to push/pull tasks and annotations programmatically.
- **Dashboards** — per-project progress (tasks completed/in review/approved), per-annotator throughput and QA metrics.
- **AI-assisted labeling** — pre-annotation via a model (auto box/polygon suggestion), which the annotator accepts/edits/rejects rather than drawing from scratch.

This spec adapts these patterns into a leaner, self-hosted tool focused only on box + polygon geometry.

---

## 3. User Roles & Permissions

| Role | Capabilities |
|---|---|
| **Admin** | Full access: user management, project creation/config, class schema management, global dashboards, import/export, storage/model config |
| **Project Manager / Lead** | Create/configure projects they own, manage class schema for their project, assign tasks/images to annotators, view project dashboards, approve/reject reviewed work, trigger export jobs |
| **Reviewer / QA** | View assigned tasks in review queue, approve / reject / request changes with comments, view annotator-level QA metrics |
| **Annotator** | View only their assigned tasks/images, annotate (box/polygon), apply classes/tags, use AI-assist, submit for review, see personal progress |

Requirements:
- Session-based or JWT auth; passwords hashed (bcrypt/argon2); optional SSO/LDAP as a stretch goal only if the org already uses it locally.
- Per-project membership and role assignment (a user's role can differ per project).
- Audit log of who created/edited/deleted/approved which annotation and when.

---

## 4. Core Domain Model

```
Organization/Instance
 └── Project
      ├── Class Schema (Classes + optional Attributes per class)
      ├── Members (user + role)
      ├── Dataset Folder(s) / Batches
      │     └── Image
      │           └── Task (status, assignee, reviewer, history)
      │                 └── Annotation (geometry: bbox | polygon, class_id,
      │                                 attributes[], tags[], z-order,
      │                                 created_by, updated_by, version)
      ├── Import Jobs (source format, mapping, status, log)
      └── Export Jobs (target format, filter, status, output artifact)
```

Key modeling requirements:
- An **Image** can belong to a Project and optionally a **Batch/Folder** (sub-grouping for large datasets, e.g. by upload session or source).
- A **Task** is the unit of assignment/workflow state, one-to-one with an Image within a Project (matches FastLabel's task=image model).
- **Annotations** are versioned or at minimum retain an edit history (who/when) for QA traceability — required for a 15–20 person team where disputes/QA corrections are common.
- **Classes** are project-scoped (not globally shared by default), each with: name, color, hotkey, optional parent/group, optional attribute schema (enum/text/boolean/number), active/deprecated flag.
- **Tags** — lightweight, free-form or predefined labels attachable at the **image level** (not just per-object), used for triage, dataset curation, and AI-suggested metadata (e.g. "blurry", "night", "contains-occlusion").
- Soft-delete for classes/images/annotations where feasible, to avoid destructive data loss during active labeling.

---

## 5. Project / Task / Image / Class Management

### 5.1 Project Management
- Create/edit/archive projects; set project type (Bounding Box / Polygon / Both).
- Define/edit class schema per project (add/edit/deactivate classes; reordering; bulk import of class list via CSV/JSON).
- Assign members and roles per project.
- Configure workflow: single-stage (annotate → done) vs two-stage (annotate → review → approve/reject/rework).
- Project-level settings: min/max polygon points, required attributes, guideline/instructions doc or rich-text panel visible to annotators inside the workspace.

### 5.2 Dataset / Image Management
- Bulk image upload (drag-and-drop, folder upload, ZIP upload with auto-extraction).
- Image list view: thumbnail grid and table view, with filters (status, assignee, class present, tag, date, batch).
- Search by filename, tag, class, annotation count.
- Batch operations: bulk assign, bulk status change, bulk delete, bulk tag, bulk move between batches.
- Pagination/virtualized rendering for datasets in the thousands-to-tens-of-thousands range without UI degradation.
- Duplicate-image detection (hash-based) as a data-quality aid.

### 5.3 Task Management
- Task states: `Not Started → In Progress → Submitted → In Review → Approved / Rejected (→ back to In Progress)`.
- Assignment: manual (PM assigns specific images to specific annotators) and auto-distribution (round-robin / balanced queue across available annotators).
- "My Queue" view for annotators — only their assigned, incomplete tasks, in priority order.
- Reviewer queue — only submitted tasks awaiting review, with side-by-side diff of what was annotated.
- Comments/notes thread per task for reviewer ↔ annotator feedback loop.
- Locking: a task open by one user should be locked/flagged to avoid concurrent-edit collisions.

### 5.4 Class Management
- Central class list per project with usage counts (# annotations using this class).
- Rename-with-propagation, merge two classes, deactivate (soft-hide) a class without deleting historical annotations.
- Class color and keyboard shortcut editable from the UI.
- Attribute schema editor (per class): add attribute (text/select/multi-select/boolean/number), mark required/optional.

### 5.5 Dashboards
- **Project dashboard**: total images, tasks by status (funnel/bar chart), annotations per class (distribution), completion % over time (burndown), average time-per-task.
- **Team/annotator dashboard**: tasks completed per annotator per day/week, review pass/reject rate per annotator, average annotation time, leaderboard (optional, admin-toggle).
- **QA dashboard**: rejection reasons breakdown, re-work rate, class-confusion patterns if derivable (e.g. classes frequently corrected into other classes).
- Exportable dashboard data (CSV) for reporting outside the tool.

---

## 6. Annotation Workspace (Core Editor) — UX Requirements

This is the highest-leverage screen since annotators live here all day; UX quality directly drives throughput.

### 6.1 Canvas & Navigation
- Pan/zoom (scroll-to-zoom, keyboard/toolbar zoom-to-fit, zoom-to-selection).
- Smooth rendering at high resolution (large images, e.g. 4K+) without lag — should use canvas/WebGL rendering rather than naive DOM/SVG-per-point for large annotation counts.
- Next/previous image navigation via keyboard (e.g. `A`/`D` or arrow keys) without full page reload.
- Minimap or thumbnail filmstrip for quick jump between images in the current task queue.
- Brightness/contrast adjustment for the loaded image (helps annotation on low-quality source images).

### 6.2 Bounding Box Tool
- Draw with click-drag; resize via corner/edge handles; move via drag; delete via keyboard (`Del`/`Backspace`).
- Snap-to-edges/pixel-precision toggle.
- Copy/paste box (with or without offset) for repeated similar-size objects.
- Numeric fine-tune (arrow-key nudge, and optionally direct x/y/w/h numeric input) for pixel-perfect correction.
- Class assignment: on-draw class picker (searchable dropdown) + hotkey-based class switch (as in V7's pattern) so annotators don't leave the keyboard.

### 6.3 Polygon Tool
- Point-by-point click to build polygon; double-click or `Enter` to close.
- Edit existing polygon: drag individual vertices, add point mid-edge, delete vertex, drag whole shape.
- Support multi-part polygons and holes (donut shapes) for objects with internal cutouts.
- Magnetic/snap-to-edge assist (basic edge-detection snapping) as a "smart" mode, distinct from full AI-segmentation (see §7).
- Simplify/smooth polygon action to reduce excessive points.
- Self-intersection warning/validation before save.

### 6.4 Shared Object Interactions
- Layer/z-order list per image (select, hide/show, lock individual annotation objects) — important once dozens of boxes/polygons overlap.
- Object list panel: all annotations on the current image with class, quick-select, quick-delete.
- Attribute editing panel for the selected object (per class-defined attribute schema).
- Undo/redo (multi-step) and autosave (debounced), with a visible "saved" state indicator.
- Keyboard-shortcut cheat-sheet overlay (configurable shortcuts is a stretch goal; fixed sane defaults is the baseline requirement).
- Image-level tag input field (separate from per-object classes) for image-wide metadata.
- "Mark as empty / no objects" action for images with nothing to annotate (so they don't sit ambiguously incomplete).

---

## 7. AI-Assisted / Semi-Automated Annotation

This is a required capability, not optional, and should be treated as a first-class subsystem, not a bolt-on.

### 7.1 Requirements
1. **Pre-labeling / auto-annotation**: run an object-detection or promptable-segmentation model (e.g. a YOLO-family detector for boxes, and a Segment-Anything-style model for polygons/masks-to-polygon) against an image or batch of images, producing draft annotations that land in the task as *suggested* (visually distinct, e.g. dashed outline) rather than committed.
2. **Human-in-the-loop confirmation**: annotator can accept-all, accept-individually, edit-then-accept, or reject each suggestion. Suggestions never auto-commit without a human action, to protect label quality.
3. **Interactive smart-polygon ("click-to-segment")**: click inside/around an object → model returns a candidate polygon mask the annotator can accept or refine (SAM-style point/box prompt workflow) — this is the single highest-value AI feature for polygon-heavy teams.
4. **Class/tag suggestion**: model-suggested class label for a drawn box/polygon (image-crop classification) and model-suggested image-level tags, surfaced as selectable chips rather than forced values.
5. **Batch pre-annotation jobs**: PM/Admin can trigger "run auto-annotation on all Not Started images in this project" as an async background job with progress tracking, rather than only per-image inference.
6. **Model management**: ability to select which model/version powers auto-annotation per project (e.g. a general pretrained detector vs a project-specific fine-tuned checkpoint), with the inference service decoupled from the main API (separate service/process, callable via internal API) so heavy model inference doesn't block the web app.
7. **Accuracy/precision framing**: track acceptance vs rejection/edit-rate of AI suggestions as a dashboard metric — this is the practical proxy for "as accurate and precise as possible" and lets the org see whether the assist model is actually helping or generating noisy overhead that annotators must clean up.
8. **Confidence display**: show a confidence score on suggested annotations so annotators can prioritize review of low-confidence ones.

### 7.2 Non-goals (to keep scope honest)
- No requirement to train/fine-tune models from within the app UI at v1 — that's a later iteration once there's enough accepted-annotation volume to fine-tune on. The app should just make it easy to **export accepted annotations** in a form usable to later fine-tune a model (closing the loop) rather than build a full active-learning/MLOps pipeline in-house.

---

## 8. Import / Export

### 8.1 Import
- **COCO JSON** (images + annotations + categories) → maps to Project classes (with a mapping UI when class names don't match exactly).
- **YOLO** (images + `.txt` label files + `classes.txt`/`data.yaml`) → box import; polygon-YOLO (segmentation format) supported if project type includes polygon.
- **CSV** (flat row-per-annotation: filename, class, x/y/w/h or polygon points) for simple bulk pre-labeled or externally-produced data.
- **ZIP bundle** containing images + one of the above annotation formats + a classes/labels manifest, auto-detected and validated on upload with a pre-import summary (X images found, Y annotations parsed, Z classes matched/unmatched) before committing.
- Import validation & error reporting: malformed files, out-of-bounds coordinates, unknown classes surfaced clearly, with an option to auto-create missing classes or map to existing ones.
- Import as an async job (progress bar, cancel, log/report of what happened) — not a blocking request, since bundles can be large.

### 8.2 Export
- **COCO JSON** — standard `images`/`annotations`/`categories` structure; polygons as `segmentation` polygons, boxes as `bbox`.
- **YOLO** — per-image `.txt` normalized-coordinate files + `classes.txt`/`data.yaml`, box format and polygon/segmentation format.
- **CSV** — flat tabular export for spreadsheet-based review or custom pipelines.
- **ZIP** — full bundle: images (optional, since sometimes only annotations are needed) + annotations JSON + classes JSON + a manifest (export date, project, filter used, counts) — mirrors FastLabel's downloadable-archive export pattern.
- **Pascal VOC XML** — common enough to include even though not explicitly requested, low incremental cost.
- Export filters: by task status (e.g. only Approved), by class, by date range, by tag, by batch — so a PM can export "only QA-approved data" for model training rather than everything.
- Export as an async job with a downloadable artifact + history of past exports (who ran it, when, with what filter) for reproducibility.
- API/SDK-style programmatic export endpoint (not only UI-triggered) so exports can be scripted/scheduled — matches FastLabel's SDK-driven workflow.

---

## 9. Non-Functional Requirements

### 9.1 Deployment
- Runs fully **locally / on-prem**: single-command deployment via `docker-compose` (API + DB + object storage + optional inference service + reverse proxy/frontend).
- No hard runtime dependency on external SaaS APIs; any AI-assist model should be runnable locally (open-weights) with cloud-API assist as an optional, swappable config rather than a requirement.
- Environment-based configuration (`.env`) for storage paths, DB connection, model paths/endpoints, auth secrets.
- Data persisted on local disk / mounted volume (images) + relational DB (metadata/annotations), with a documented backup procedure.

### 9.2 Backend (FastAPI)
- FastAPI as the primary REST API framework; Pydantic models for request/response validation; async endpoints for I/O-bound operations (uploads, exports, model calls).
- Background job execution for long-running work (imports, exports, batch AI-annotation) via a task queue (e.g. Celery/RQ/ARQ or FastAPI `BackgroundTasks` for lighter jobs) so the API stays responsive for 15–20 concurrent users.
- WebSocket or polling-based endpoint for live task/lock status and job progress updates in the UI.
- OpenAPI docs auto-generated (FastAPI default) and kept accurate — used as the contract for the SDK/automation layer.
- Database: relational (PostgreSQL recommended for local deployment) with migrations (Alembic) tracked in-repo.

### 9.3 Performance & Scale Targets (for this team size)
- Support datasets in the tens of thousands of images per project without list/grid view degradation (pagination + indexed queries, not full-table client-side loads).
- Support images with hundreds of annotation objects per image without editor lag (canvas-level rendering optimizations, not one DOM node per point).
- Support 15–20 concurrent editing sessions without lock contention or data loss (task-level locking, optimistic concurrency on annotation saves).

### 9.4 Data Integrity & QA
- No silent data loss: autosave failures must surface to the user, not fail silently.
- Full audit trail per annotation (created/updated/by whom/when) sufficient to answer "who labeled this and when" for any QA dispute.
- Review workflow enforces that rejected tasks return to the original annotator with reviewer comments visible.

### 9.5 Usability / Accessibility
- Keyboard-first workflow for annotators (mouse-only should still work, but hotkeys are the expected fast path).
- Responsive to different screen sizes for the workspace at least down to laptop-class displays (not required to be mobile-optimized — this is a desktop-browser tool).
- Clear, consistent iconography/status coloring for task states across list views and dashboards.

---

## 10. Explicit Out-of-Scope (v1)

To keep this comparable against a realistic local team tool rather than FastLabel's full enterprise platform:

- Video, 3D/point cloud, audio, and text annotation modalities.
- Outsourced/marketplace labeling workforce management.
- Full MLOps (model training/versioning/deployment pipeline) inside the app.
- Multi-tenant SaaS billing/organization-switching.
- Non-box/non-polygon geometries (keypoints, lines, cuboids, masks-as-brush) — unless the org later decides to extend project types; schema should not preclude this, but v1 UI/workflow is scoped to box + polygon only.

---

## 11. How This Should Be Used

Feed this document alongside the target codebase's `discovery.md` (or run direct source analysis) to produce a **gap report** structured as:

1. **Feature coverage matrix** — for each section above (§3–§9), mark: *Implemented / Partially Implemented / Missing / Implemented but architecturally fragile*.
2. **Architecture assessment** — does the current backend actually use FastAPI idiomatically (async, Pydantic, background jobs, migrations), and does the data model support the Project → Task → Image → Annotation hierarchy with class/attribute schema and audit history described in §4?
3. **UX assessment** — does the annotation workspace meet §6's canvas/tool/keyboard requirements, and do project/task/dashboard screens meet §5?
4. **AI-assist assessment** — is there any pre-labeling/smart-polygon/class-suggestion capability at all (§7), and if so, is it decoupled from the main request/response cycle appropriately?
5. **Import/export assessment** — which of COCO/YOLO/CSV/ZIP/VOC are actually implemented correctly (round-trip tested), per §8.
6. **Prioritized remediation plan** — ordered by (a) data-integrity/architecture risks first, (b) core annotator-workflow blockers second, (c) AI-assist and dashboard/reporting gaps third, with rough effort sizing per item.
