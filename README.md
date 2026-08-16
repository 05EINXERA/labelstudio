# Image Annotation Workspace

A browser-based workspace for AI-assisted image annotation, featuring state-of-the-art zero-shot detection and segmentation models powered by a high-concurrency FastAPI backend.

## Architecture & Tech Stack

- **Frontend**: Vanilla JavaScript and HTML5 Canvas.
- **Backend**: FastAPI (Python), serving concurrent ML inferences using an Asynchronous Job Polling Queue to prevent timeouts.
- **Database**: SQLite (WAL) for development, or **PostgreSQL** for multi-user LAN deployments (configured via `DATABASE_URL`).
- **AI Models**:
  - **YOLO-World / YOLOv8**: Zero-shot object detection (Auto-Detect).
  - **Meta SAM (Segment Anything Model)**: Pixel-perfect polygon segmentation (Magic Wand).
  - **OpenAI CLIP**: Zero-shot image classification (Auto-Tagging).

## Setup

1. Copy `.env.example` to `.env` and configure your settings (e.g., `DATABASE_URL`, `JWT_SECRET`).

2. Install Python dependencies:

```powershell
.\venv\Scripts\pip.exe install -r requirements.txt
```

3. Start the local FastAPI server using the provided launch script (which loads `.env`):

```powershell
.\scripts\run.ps1
```

Alternatively, you can run Uvicorn directly if your environment is already set up:
```powershell
.\venv\Scripts\uvicorn.exe main:app --port 8000
```

## Core Features

- **Project Dashboard**: A comprehensive interface to manage tasks, datasets, and your project's taxonomy (classes). Includes full CRUD operations and JSON import/export functionality.
- **Precision Annotation Engine**: A custom-built HTML5 Canvas engine supporting precise bounding boxes and robust polygon drawing (with accurate starting-point closure and vertex manipulation).
- **Team Management & Access Control**: Robust decentralized access control where team creators manage their own memberships. Strict data visibility isolates teams, while allowing seamless cross-team member assignments.
- **Auto-Detect**: Detect all objects in an image instantly using YOLOv8 or YOLO-World.
- **Magic Wand**: Click any object to automatically generate precise polygon masks using Meta's Segment Anything Model (SAM). Output is automatically smoothed using Ramer-Douglas-Peucker and Chaikin curves for natural, organic boundaries.
- **Auto-Tag**: Automatically assign scene and object tags to your images using CLIP zero-shot classification.
- **Concurrent Workspace**: Safely work across multiple browser tabs with real-time SQLite database synchronization and conflict resolution (Optimistic Locking).
- **Time Tracking**: Accurately tracks active session time spent annotating per user and task.
- **AI Job Queue**: AI inference runs in a decoupled background queue, allowing multiple users to trigger heavy ML models simultaneously without locking up the server or timing out HTTP requests.
- **Production Ready**: Configured with caching middleware and robust API endpoints for deploying into production environments.


## Troubleshooting

### Git Push Failing (Large Files)
If you try to push this project to GitHub and it fails with a `Large files detected` error, it means you accidentally committed one of the heavy `.onnx`, `.pt`, or `.pth` AI models to your Git history. 

To fix this:
1. Ensure `models/*.onnx` and `models/*.pt` are in your `.gitignore` file.
2. If you just committed them in your last commit, you can remove them from tracking and amend the commit:
```bash
git rm -r --cached models/
git commit --amend -C HEAD
git push origin main
```

## Configuration & Environment Variables

You can configure the application using the following environment variables (set them in your `.env` file):

- `DATABASE_URL`: Connection string for the database. Use `sqlite:///workspace.db` for local development, or a `postgresql://` URL for the production LAN deployment.
- `DATA_DIR`: Defines where uploaded images and exports are stored. Defaults to `.` (the current directory).
- `JWT_SECRET`: Used for securely signing tokens for authentication. Ensure you change this to a strong, random string in production. **Never commit `.jwt_secret` or `.env` to version control.**

## Deployment

This project is designed to be deployed on a single trusted PC on an office LAN, serving ~20–25 annotators sharing a single login. 

1. Install **PostgreSQL** on the deployment machine and create an empty database.
2. Configure `.env` with the `DATABASE_URL` pointing to your Postgres instance.
3. Run Alembic migrations to build the schema: `alembic upgrade head`.
4. Run the application via the provided `scripts/run.ps1` script (or `uvicorn main:app --host 0.0.0.0 --port 8000`).
5. **Note:** Do not run Uvicorn with multiple workers (`--workers N`). The application must run as a single process due to in-memory ML models and task locking state.
