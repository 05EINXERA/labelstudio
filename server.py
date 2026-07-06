import json
import os
import sqlite3
import uuid
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, Query, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from label_studio_sdk import LabelStudio
from detector import DetectionClientError, detect_objects

HOST = "127.0.0.1"
PORT = int(os.environ.get("APP_PORT", "8765"))
LABEL_STUDIO_URL = os.environ.get("LABEL_STUDIO_URL", "http://localhost:8000/")
LABEL_STUDIO_API_KEY = os.environ.get("LABEL_STUDIO_API_KEY", "")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic Models ---

class WorkspaceData(BaseModel):
    key: str
    value: str

class Project(BaseModel):
    name: str
    slug: str
    type: str = "Image - Polygon"
    creator: str

class TaskUpdate(BaseModel):
    id: Optional[int] = None
    assignee: Optional[str] = None
    status: Optional[str] = "New"
    description: Optional[str] = None
    time_spent_delta: Optional[int] = 0
    annotations: Optional[str] = None

class BulkDelete(BaseModel):
    ids: List[int]

class BulkUpdate(BaseModel):
    ids: List[int]
    assignee: Optional[str] = None
    status: Optional[str] = None

class TeamMember(BaseModel):
    name: str

class TeamTime(BaseModel):
    name: str
    time_logged: int

class DetectPayload(BaseModel):
    image: str
    selection: Optional[List[dict]] = None

class LabelStudioPayload(BaseModel):
    projectId: Optional[str] = None
    taskId: Optional[str] = None
    taskData: Optional[dict] = None
    result: Optional[list] = None

# --- Database ---

def init_db():
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS workspace_data (key TEXT PRIMARY KEY, value TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        name TEXT, 
        slug TEXT, 
        type TEXT, 
        status TEXT, 
        creator TEXT, 
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        project_id INTEGER,
        image_path TEXT,
        description TEXT, 
        status TEXT,
        assignee TEXT,
        time_spent INTEGER DEFAULT 0,
        updated_at DATETIME,
        annotations TEXT
    )''')
    
    # Migrations (safely ignore if exists)
    for col in [
        "ALTER TABLE tasks ADD COLUMN project_id INTEGER",
        "ALTER TABLE tasks ADD COLUMN image_path TEXT",
        "ALTER TABLE tasks ADD COLUMN status TEXT",
        "ALTER TABLE tasks ADD COLUMN time_spent INTEGER DEFAULT 0",
        "ALTER TABLE tasks ADD COLUMN updated_at DATETIME",
        "ALTER TABLE tasks ADD COLUMN annotations TEXT"
    ]:
        try:
            c.execute(col)
        except sqlite3.OperationalError:
            pass

    c.execute('''CREATE TABLE IF NOT EXISTS team_members (name TEXT PRIMARY KEY, time_logged INTEGER)''')
    conn.commit()
    conn.close()

init_db()

# --- API Endpoints ---

@app.get("/api/data")
def get_data():
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    c.execute("SELECT key, value FROM workspace_data")
    rows = c.fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}

@app.post("/api/data")
def set_data(data: WorkspaceData):
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO workspace_data (key, value) VALUES (?, ?)", (data.key, data.value))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/api/projects")
def get_projects():
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    c.execute("SELECT id, name, slug, type, status, creator, created_at FROM projects")
    projects = [{"id": row[0], "name": row[1], "slug": row[2], "type": row[3], "status": row[4], "creator": row[5], "created_at": row[6]} for row in c.fetchall()]
    conn.close()
    return projects

@app.get("/api/projects/{project_id}/metrics")
def get_project_metrics(project_id: int):
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM tasks WHERE project_id = ?", (project_id,))
    total = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM tasks WHERE project_id = ? AND status = 'Completed'", (project_id,))
    completed = c.fetchone()[0]
    
    progress = int((completed / total * 100)) if total > 0 else 0
    
    if total > 0 and completed == total:
        c.execute("UPDATE projects SET status = 'Completed' WHERE id = ?", (project_id,))
        conn.commit()
    elif completed > 0:
        c.execute("UPDATE projects SET status = 'In Progress' WHERE id = ?", (project_id,))
        conn.commit()

    conn.close()
    return {"total": total, "completed": completed, "progress": progress}

@app.post("/api/projects")
def create_project(project: Project):
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    c.execute("INSERT INTO projects (name, slug, type, status, creator) VALUES (?, ?, ?, ?, ?)", 
              (project.name, project.slug, project.type, "Preparing", project.creator))
    project_id = c.lastrowid
    conn.commit()
    conn.close()
    return {"id": project_id, "status": "ok"}

@app.post("/api/projects/{project_id}/upload")
def upload_files(project_id: int, assignee: Optional[str] = Query(None), file: List[UploadFile] = File(...)):
    os.makedirs("uploads", exist_ok=True)
    saved_files = []
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    
    ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
    
    for f in file:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"File type {ext} is not allowed.")
            
        new_filename = f"{uuid.uuid4().hex}{ext}"
        filepath = os.path.join("uploads", new_filename)
        
        with open(filepath, "wb") as out_file:
            out_file.write(f.file.read())
            
        c.execute("INSERT INTO tasks (project_id, image_path, description, status, assignee) VALUES (?, ?, ?, ?, ?)", 
                  (project_id, filepath, f.filename, 'New', assignee))
        saved_files.append(filepath)
        
    conn.commit()
    conn.close()
    return {"status": "ok", "files": saved_files}

@app.get("/api/tasks")
def get_tasks(projectId: Optional[int] = Query(None)):
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    if projectId:
        c.execute("SELECT id, description, assignee, image_path, status, time_spent, updated_at, annotations FROM tasks WHERE project_id = ?", (projectId,))
    else:
        c.execute("SELECT id, description, assignee, image_path, status, time_spent, updated_at, annotations FROM tasks")
    
    tasks = []
    for row in c.fetchall():
        annotations_data = []
        if row[7]:
            try:
                annotations_data = json.loads(row[7])
            except:
                pass
        tasks.append({
            "id": row[0], "description": row[1], "assignee": row[2], 
            "image_path": row[3], "status": row[4], "time_spent": row[5], 
            "updated_at": row[6], "annotations": annotations_data
        })
    conn.close()
    return tasks

@app.post("/api/tasks")
def update_or_create_task(task: TaskUpdate, projectId: Optional[int] = Query(None)):
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    if task.id:
        c.execute("UPDATE tasks SET assignee = ?, status = ?, description = COALESCE(?, description), time_spent = COALESCE(time_spent, 0) + ?, annotations = COALESCE(?, annotations), updated_at = CURRENT_TIMESTAMP WHERE id = ?", 
                  (task.assignee, task.status, task.description, task.time_spent_delta, task.annotations, task.id))
        task_id = task.id
    else:
        c.execute("INSERT INTO tasks (description, assignee, project_id, status, time_spent, annotations, updated_at) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)", 
                  (task.description, task.assignee, projectId, task.status, task.time_spent_delta, task.annotations))
        task_id = c.lastrowid
    conn.commit()
    conn.close()
    return {"id": task_id, "status": "ok"}

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/api/tasks/bulk-delete")
def bulk_delete_tasks(payload: BulkDelete):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No ids provided")
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    c.execute(f"DELETE FROM tasks WHERE id IN ({','.join('?' * len(payload.ids))})", tuple(payload.ids))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/api/tasks/bulk-update")
def bulk_update_tasks(payload: BulkUpdate):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No ids provided")
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    if payload.assignee is not None and payload.status is not None:
        c.execute(f"UPDATE tasks SET assignee = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE id IN ({','.join('?' * len(payload.ids))})", (payload.assignee, payload.status, *payload.ids))
    elif payload.assignee is not None:
        c.execute(f"UPDATE tasks SET assignee = ?, updated_at = CURRENT_TIMESTAMP WHERE id IN ({','.join('?' * len(payload.ids))})", (payload.assignee, *payload.ids))
    elif payload.status is not None:
        c.execute(f"UPDATE tasks SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id IN ({','.join('?' * len(payload.ids))})", (payload.status, *payload.ids))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/api/team")
def get_team():
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    c.execute("SELECT name, time_logged FROM team_members")
    team = [{"name": row[0], "time_logged": row[1]} for row in c.fetchall()]
    conn.close()
    return team

@app.post("/api/team")
def create_team_member(member: TeamMember):
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO team_members (name, time_logged) VALUES (?, 0)", (member.name,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.delete("/api/team/{name}")
def delete_team_member(name: str):
    import urllib.parse
    name = urllib.parse.unquote(name)
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    c.execute("DELETE FROM team_members WHERE name = ?", (name,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/api/team/time")
def update_team_time(payload: TeamTime):
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    c.execute("UPDATE team_members SET time_logged = ? WHERE name = ?", (payload.time_logged, payload.name))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/api/detect")
def detect(payload: DetectPayload):
    try:
        response = detect_objects(payload.image, selection=payload.selection)
        return response
    except DetectionClientError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception:
        raise HTTPException(status_code=500, detail="Object detection failed.")

@app.post("/api/label-studio/send")
def send_to_ls(payload: LabelStudioPayload):
    if not LABEL_STUDIO_API_KEY:
        raise HTTPException(status_code=400, detail="Set LABEL_STUDIO_API_KEY before starting server.py.")
    if not payload.taskId and not payload.projectId:
        raise HTTPException(status_code=400, detail="Send projectId to create a task, or taskId to annotate an existing task.")
    if not payload.taskData:
        raise HTTPException(status_code=400, detail="Missing taskData.")
    if not payload.result:
        raise HTTPException(status_code=400, detail="Missing annotation result.")

    try:
        client = LabelStudio(
            base_url=LABEL_STUDIO_URL,
            api_key=LABEL_STUDIO_API_KEY,
        )

        task_id = payload.taskId
        if not task_id:
            task = client.tasks.create(data=payload.taskData, project=int(payload.projectId))
            task_id = str(task.id)

        annotation = client.annotations.create(
            int(task_id),
            result=payload.result,
            was_cancelled=False,
            ground_truth=False,
        )

        return {
            "taskId": task_id,
            "annotationId": annotation.id,
            "labelStudioUrl": LABEL_STUDIO_URL,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Label Studio sync failed.")

# --- Static Files ---

@app.get("/")
def read_index():
    return FileResponse("index.html")

@app.get("/{filename}.html")
def read_html(filename: str):
    return FileResponse(f"{filename}.html")

@app.get("/{filename}.js")
def read_js(filename: str):
    return FileResponse(f"{filename}.js")

@app.get("/{filename}.css")
def read_css(filename: str):
    return FileResponse(f"{filename}.css")

@app.get("/{filename}.png")
def read_png(filename: str):
    return FileResponse(f"{filename}.png")

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

if __name__ == "__main__":
    import uvicorn
    print(f"App running at http://{HOST}:{PORT}/")
    print(f"Label Studio target: {LABEL_STUDIO_URL}")
    print("Object detection: YOLOv8 ONNX via OpenCV DNN")
    uvicorn.run("server:app", host=HOST, port=PORT, reload=False)
