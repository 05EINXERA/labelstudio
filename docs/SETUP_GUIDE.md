# Setup Guide — Zero to Working

Target: a running annotation workspace with all three AI features functional. Commands are PowerShell (Windows, the primary dev environment); bash equivalents are noted where they differ.

---

## 0. Prerequisites

| Need | Version | Check |
|---|---|---|
| Python | 3.10–3.12 (venv here is 3.12.10; Render uses 3.10.12) | `python --version` |
| Git | any | `git --version` |
| Disk | ~8 GB free | weights + torch are large |
| GPU | optional | CUDA is used automatically if present; CPU works, just slower |

No Node, npm, or build step — the frontend is served as static files.

---

## 1. Clone and create the virtualenv

```powershell
git clone <repo-url> labelstudio
cd labelstudio
python -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
```

The project convention is to call venv binaries by **explicit path** (`.\venv\Scripts\pip.exe`) rather than activating. Either works; activation is fine if you prefer:

```powershell
.\venv\Scripts\Activate.ps1     # bash: source venv/bin/activate
```

---

## 2. Install dependencies

```powershell
.\venv\Scripts\pip.exe install -r requirements.txt
```

### ⚠️ requirements.txt is incomplete — you must install two more packages

`detector.py` imports `ultralytics` and `transformers`, but neither is listed in [requirements.txt](requirements.txt). Without them the server starts and basic ONNX detection works, but **Magic Wand, YOLO-World prompts, and Auto-Tag all fail at runtime** with `RuntimeError: Please install ultralytics and torch...`.

```powershell
.\venv\Scripts\pip.exe install ultralytics transformers
```

Consider adding both to `requirements.txt` as your first commit.

### Note on torch

`requirements.txt` pins `torch>=2.0.0`, which pulls the default CPU wheel (~2 GB). For CUDA:

```powershell
.\venv\Scripts\pip.exe install torch --index-url https://download.pytorch.org/whl/cu121
```

### Note on bcrypt

The code in [api/auth.py](api/auth.py) imports `bcrypt` **directly**, not through passlib, even though `requirements.txt` lists `passlib[bcrypt]`. `passlib` installs `bcrypt` transitively so this works, but if you ever trim passlib, keep `bcrypt` explicitly.

---

## 3. Model weights

**You do not need to download anything manually.** Weights are gitignored and fetched on first use. Here's what happens and where things land, so you can pre-seed them if you're working offline or in CI.

| Model | Used by | Size | How it arrives |
|---|---|---|---|
| `yolov8n-seg.onnx` | Auto-Detect | ~13 MB | `ensure_model_file()` downloads the `.pt` from the Ultralytics GitHub release into `models/`, then auto-exports it to ONNX via `ultralytics` |
| `yolov8s-worldv2.pt` | prompt-based detect | ~25 MB | Ultralytics downloads on first YOLO-World call |
| `mobile_sam.pt` | Magic Wand | ~40 MB | Ultralytics downloads on first SAM call (already present in repo root) |
| `facebook/sam2-hiera-large` | Magic Wand (SAM2 option) | ~900 MB | HuggingFace Hub → `~/.cache/huggingface` |
| `openai/clip-vit-base-patch32` | Auto-Tag | ~600 MB | HuggingFace Hub → `~/.cache/huggingface` |

The ONNX export step **requires `ultralytics`**. If it's missing, `ensure_model_file()` raises a message telling you to install it or run `yolo export model=... format=onnx` yourself.

This working copy already ships `FastSAM-s.pt`, `mobile_sam.pt`, `yolov8n.pt`, and `models/yolov8n-seg.onnx`, so a fresh clone will download more than this checkout does.

### Optional: pre-warm the HuggingFace models

First Auto-Tag or SAM2 click otherwise stalls for minutes with no UI feedback.

```powershell
.\venv\Scripts\python.exe -c "from transformers import CLIPModel, CLIPProcessor; CLIPModel.from_pretrained('openai/clip-vit-base-patch32'); CLIPProcessor.from_pretrained('openai/clip-vit-base-patch32')"
```

### Optional: override model sources

```powershell
$env:YOLO_MODEL = "yolov8n-seg.onnx"
$env:YOLO_download_url = "https://.../yolov8n-seg.pt"
$env:YOLO_WORLD_MODEL = "yolov8s-worldv2.pt"
```

---

## 4. Database

**SQLite — no server to install, no credentials to configure.** `Base.metadata.create_all()` runs at import in [main.py](main.py#L15), so `workspace.db` and every table are created on first start. WAL mode and `synchronous=NORMAL` are set via a connect-event hook in [database.py](database.py).

For a clean slate:

```powershell
Remove-Item workspace.db, workspace.db-shm, workspace.db-wal -ErrorAction SilentlyContinue
```

Alembic is configured and has one baseline migration. `create_all` and Alembic overlap here — `create_all` will happily build the schema without Alembic ever running. Use Alembic when you change [models.py](models.py):

```powershell
.\venv\Scripts\alembic.exe upgrade head
.\venv\Scripts\alembic.exe revision --autogenerate -m "describe change"
```

---

## 5. Environment variables

None are required for local development — every one has a default. Set them for deployment.

| Variable | Default | Purpose |
|---|---|---|
| `JWT_SECRET` | read/generate `.jwt_secret` | Token signing. **Set this in production.** |
| `DATA_DIR` | `.` | Root for `workspace.db` + `uploads/`. Render sets `/data`. |
| `APP_PORT` | `8765` | Only used by `python main.py`, **not** by `uvicorn` directly |
| `CORS_ORIGINS` | `*` | Comma-separated. Lock down in production. |
| `LABEL_STUDIO_URL` | `http://localhost:8000/` | Label Studio push target |
| `LABEL_STUDIO_API_KEY` | *(empty)* | Required for the push, else 400 |
| `DETECT_CONFIDENCE` / `DETECT_NMS` / `DETECT_MAX` | 0.35 / 0.45 / 100 | Detection thresholds |
| `MAX_IMAGE_BYTES` / `MAX_IMAGE_PIXELS` | 50 MB / 50 M | Upload guardrails |

```powershell
$env:JWT_SECRET = "your-long-random-secret"
```

### Rotate the committed secret

`.jwt_secret` is **checked into the repo**. Before deploying, set a real `JWT_SECRET`, delete the file, and add `.jwt_secret` to `.gitignore`.

---

## 6. Run it

```powershell
.\venv\Scripts\uvicorn.exe main:app --reload
```

Open **http://127.0.0.1:8000/**.

Two footguns here:

- `uvicorn` defaults to **8000**; `python main.py` uses **8765** (`APP_PORT`). Use `--port` to control it explicitly.
- `LABEL_STUDIO_URL` also defaults to `http://localhost:8000/`. If you run a real Label Studio locally, move one of the two off 8000.

Never run more than one worker — the AI job queue is an in-process dict (see the Project Guide, §3).

---

## 7. First-run walkthrough

1. **Register** at `/` — first user, no seeding or admin bootstrap needed.
2. **Dashboard** → create a project (name, type, assignee).
3. **Project details** → upload images (png/jpg/jpeg/gif/webp only). One task per file.
4. **Open a task** → the annotation canvas.
5. Add labels in the label panel (name + color) — these are global, not per-project.
6. Try each AI assist:
   - **Auto-Detect** — first click triggers the YOLO download + ONNX export. Expect a minute or two, watch the terminal.
   - **Magic Wand** — click an object; first click pulls SAM weights.
   - **Auto-Tag** — first click pulls CLIP (~600 MB).
7. **Export** → COCO JSON or CSV, generated in-browser.

---

## 8. Verifying the install

```powershell
.\venv\Scripts\python.exe check_endpoints.py     # walks the route table
.\venv\Scripts\python.exe test_sam_mask.py       # SAM smoke test
```

These are loose dev scripts, not a test suite — there's no pytest setup in the repo. Interactive API docs are at **http://127.0.0.1:8000/docs**.

---

## 9. Deploying to Render

[render.yaml](render.yaml) is ready: Python 3.10.12, `pip install -r requirements.txt`, `uvicorn main:app --host 0.0.0.0 --port $PORT`, and a 5 GB disk mounted at `/data` with `DATA_DIR=/data` so the DB and uploads survive redeploys.

Before you deploy:

1. Replace `JWT_SECRET: "change-me-in-production"` with a real secret (set it in the Render dashboard, not the YAML).
2. Add `ultralytics` and `transformers` to `requirements.txt` or the AI features are broken in production.
3. Set `CORS_ORIGINS` to your actual origin instead of `*`.
4. Note the disk is 5 GB and HF model caches are large — CLIP + SAM2 alone approach 1.5 GB.
5. Cold starts include model downloads; the first detect request on a fresh instance will be slow.

---

## 10. Troubleshooting

**Frontend changes don't appear.** All frontend files are served `no-store` — a normal refresh should always pick up edits. If not, check you're editing the file actually being served (the annotation page loads `frontend/js/init.js` and its imports, not a single `app.js`).

**`RuntimeError: Please install ultralytics and torch`.** Step 2 — install `ultralytics` and `transformers`.

**`Could not download YOLO model...`.** No network, or GitHub is unreachable. Drop the `.pt` into `models/` manually and let the ONNX export run, or set `YOLO_download_url`.

**409 on save.** Optimistic locking — another tab or user saved that task first. Refresh to pick up their annotations.

**`database is locked`.** WAL plus a 15 s timeout normally prevents this. If it persists, check for a stray second server process holding the file.

**404 from `/api/detect/status/{job_id}`.** Job results are deleted on first read, and the queue is in-memory. Either something already polled it, or the server restarted mid-job.

**Git push rejected for large files.** You committed weights. `git rm -r --cached models/` then amend — see the README.
