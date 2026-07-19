# Image Annotation Workspace

A browser-based workspace for AI-assisted image annotation, featuring state-of-the-art zero-shot detection and segmentation models powered by a high-concurrency FastAPI backend.

## Architecture & Tech Stack

- **Frontend**: Vanilla JavaScript and HTML5 Canvas.
- **Backend**: FastAPI (Python), serving concurrent ML inferences using an Asynchronous Job Polling Queue to prevent timeouts.
- **Database**: SQLite (Configured with WAL mode for safe concurrent read/writes).
- **AI Models**:
  - **YOLO-World / YOLOv8**: Zero-shot object detection (Auto-Detect).
  - **Meta SAM (Segment Anything Model)**: Pixel-perfect polygon segmentation (Magic Wand).
  - **OpenAI CLIP**: Zero-shot image classification (Auto-Tagging).

## Setup

1. Install Python dependencies:

```powershell
.\venv\Scripts\pip.exe install -r requirements.txt
```

2. Start the local FastAPI server:

```powershell
.\venv\Scripts\uvicorn.exe main:app --reload
```

Then open `http://127.0.0.1:8000/` (or whatever port Uvicorn specifies in the terminal).

## Core Features

- **Project Dashboard**: A comprehensive interface to manage tasks, datasets, and your project's taxonomy (classes). Includes full CRUD operations and JSON import/export functionality.
- **Precision Annotation Engine**: A custom-built HTML5 Canvas engine supporting precise bounding boxes and robust polygon drawing (with accurate starting-point closure and vertex manipulation).
- **Auto-Detect**: Detect all objects in an image instantly using YOLOv8 or YOLO-World.
- **Magic Wand**: Click any object to automatically generate precise polygon masks using Meta's Segment Anything Model (SAM).
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

You can configure the application using the following environment variables:

- `DATA_DIR`: Defines where the SQLite database and uploaded images are stored. Defaults to `.` (the current directory). Set this to a persistent volume path (e.g., `/data`) when deploying to production to ensure data isn't lost on restart.
- `JWT_SECRET`: Used for securely signing tokens if you implement authentication. Ensure you change this to a strong, random string in production.

## Deployment (Render.com)

This project is fully configured for seamless deployment on **Render**. It includes a `render.yaml` Blueprint that automatically sets up the Python environment, dependencies, and a **Persistent Disk** to safely store your annotation database and images.

To deploy:
1. Push your repository to GitHub.
2. Log in to [Render](https://render.com).
3. Click **New** -> **Blueprint**.
4. Connect your GitHub repository. Render will automatically read the `render.yaml` file, provision the persistent 5GB `/data` disk, and start the FastAPI server.
5. *Important*: Go to the **Environment** tab of your new service in the Render dashboard and update the `JWT_SECRET` to a secure random string.
