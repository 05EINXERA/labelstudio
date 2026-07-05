import json
import os
import sqlite3
import email
import uuid
from urllib.parse import urlparse, parse_qs
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from label_studio_sdk import LabelStudio

from detector import DetectionClientError, detect_objects


HOST = "127.0.0.1"
PORT = int(os.environ.get("APP_PORT", "8765"))
LABEL_STUDIO_URL = os.environ.get("LABEL_STUDIO_URL", "http://localhost:8000/")
LABEL_STUDIO_API_KEY = os.environ.get("LABEL_STUDIO_API_KEY", "")
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", str(25 * 1024 * 1024)))


class AnnotationServer(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == "/api/data":
            self.handle_data_get()
            return
        if path == "/api/tasks":
            query = parse_qs(parsed.query)
            project_id = query.get('projectId', [None])[0]
            self.handle_tasks_get(project_id)
            return
        if path == "/api/team":
            self.handle_team_get()
            return
        if path == "/api/projects":
            self.handle_projects_get()
            return
            
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == "/api/data":
            self.handle_data_post()
            return
        if path == "/api/tasks":
            query = parse_qs(parsed.query)
            project_id = query.get('projectId', [None])[0]
            self.handle_tasks_post(project_id)
            return
        if path == "/api/team":
            self.handle_team_post()
            return
        if path == "/api/team/time":
            self.handle_team_time_post()
            return
        if path == "/api/projects":
            self.handle_projects_post()
            return
        if path.startswith("/api/projects/") and path.endswith("/upload"):
            project_id = path.split("/")[3]
            self.handle_project_upload(project_id)
            return

        if path == "/api/detect":
            self.handle_detect()
            return

        if path == "/api/label-studio/send":
            self.handle_label_studio_send()
            return

        self.send_error(404, "Not found")

    def do_DELETE(self):
        if self.path.startswith("/api/tasks/"):
            self.handle_tasks_delete(self.path.split("/")[-1])
            return
        if self.path.startswith("/api/team/"):
            self.handle_team_delete(self.path.split("/")[-1])
            return
        self.send_error(404, "Not found")

    def handle_detect(self):
        try:
            payload = self.read_json()
            response = detect_objects(payload.get("image"), selection=payload.get("selection"))
            self.write_json(200, response)
        except DetectionClientError as error:
            self.write_json(400, {"error": str(error)})
        except Exception:
            self.write_json(500, {"error": "Object detection failed."})

    def handle_data_get(self):
        try:
            conn = sqlite3.connect("workspace.db")
            c = conn.cursor()
            c.execute("SELECT key, value FROM workspace_data")
            rows = c.fetchall()
            conn.close()
            payload = {row[0]: row[1] for row in rows}
            self.write_json(200, payload)
        except Exception as e:
            self.write_json(500, {"error": str(e)})

    def handle_data_post(self):
        try:
            payload = self.read_json()
            key = payload.get("key")
            value = payload.get("value")
            if not key or value is None:
                raise ValueError("Missing key or value.")
            
            conn = sqlite3.connect("workspace.db")
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO workspace_data (key, value) VALUES (?, ?)", (key, value))
            conn.commit()
            conn.close()
            
            self.write_json(200, {"status": "ok"})
        except Exception as e:
            self.write_json(500, {"error": str(e)})


    def handle_projects_get(self):
        try:
            conn = sqlite3.connect("workspace.db")
            c = conn.cursor()
            c.execute("SELECT id, name, slug, type, status, creator, created_at FROM projects")
            projects = [{"id": row[0], "name": row[1], "slug": row[2], "type": row[3], "status": row[4], "creator": row[5], "created_at": row[6]} for row in c.fetchall()]
            conn.close()
            self.write_json(200, projects)
        except Exception as e:
            self.write_json(500, {"error": str(e)})

    def handle_projects_post(self):
        try:
            payload = self.read_json()
            conn = sqlite3.connect("workspace.db")
            c = conn.cursor()
            c.execute("INSERT INTO projects (name, slug, type, status, creator) VALUES (?, ?, ?, ?, ?)", 
                      (payload.get("name"), payload.get("slug"), payload.get("type", "Image - Polygon"), "Preparing", payload.get("creator")))
            project_id = c.lastrowid
            conn.commit()
            conn.close()
            self.write_json(200, {"id": project_id, "status": "ok"})
        except Exception as e:
            self.write_json(500, {"error": str(e)})

    def handle_project_upload(self, project_id):
        try:
            content_type = self.headers.get("Content-Type")
            if not content_type or "multipart/form-data" not in content_type:
                self.write_json(400, {"error": "Expected multipart/form-data"})
                return
                
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            
            msg = email.message_from_bytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
            
            os.makedirs("uploads", exist_ok=True)
            saved_files = []
            
            conn = sqlite3.connect("workspace.db")
            c = conn.cursor()
            
            for part in msg.walk():
                filename = part.get_filename()
                if filename:
                    ext = os.path.splitext(filename)[1]
                    new_filename = f"{uuid.uuid4().hex}{ext}"
                    filepath = os.path.join("uploads", new_filename)
                    with open(filepath, "wb") as f:
                        f.write(part.get_payload(decode=True))
                    
                    c.execute("INSERT INTO tasks (project_id, image_path, description, status) VALUES (?, ?, ?, ?)", 
                              (project_id, filepath, filename, 'New'))
                    saved_files.append(filepath)
                    
            conn.commit()
            conn.close()
            self.write_json(200, {"status": "ok", "files": saved_files})
        except Exception as e:
            self.write_json(500, {"error": str(e)})

    def handle_tasks_get(self, project_id=None):
        try:
            conn = sqlite3.connect("workspace.db")
            c = conn.cursor()
            if project_id:
                c.execute("SELECT id, description, assignee, image_path, status FROM tasks WHERE project_id = ?", (project_id,))
            else:
                c.execute("SELECT id, description, assignee, image_path, status FROM tasks")
            tasks = []
            for row in c.fetchall():
                tasks.append({"id": row[0], "description": row[1], "assignee": row[2], "image_path": row[3], "status": row[4]})
            conn.close()
            self.write_json(200, tasks)
        except Exception as e:
            self.write_json(500, {"error": str(e)})

    def handle_tasks_post(self, project_id=None):
        try:
            payload = self.read_json()
            conn = sqlite3.connect("workspace.db")
            c = conn.cursor()
            # If it's an update to an existing task (e.g. status or assignee change)
            if "id" in payload:
                c.execute("UPDATE tasks SET assignee = ?, status = ? WHERE id = ?", (payload.get("assignee"), payload.get("status", "New"), payload.get("id")))
                task_id = payload.get("id")
            else:
                c.execute("INSERT INTO tasks (description, assignee, project_id, status) VALUES (?, ?, ?, ?)", 
                          (payload.get("description"), payload.get("assignee"), project_id, payload.get("status", "New")))
                task_id = c.lastrowid
            conn.commit()
            conn.close()
            self.write_json(200, {"id": task_id, "status": "ok"})
        except Exception as e:
            self.write_json(500, {"error": str(e)})

    def handle_tasks_delete(self, task_id):
        try:
            conn = sqlite3.connect("workspace.db")
            c = conn.cursor()
            c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            conn.close()
            self.write_json(200, {"status": "ok"})
        except Exception as e:
            self.write_json(500, {"error": str(e)})

    def handle_team_get(self):
        try:
            conn = sqlite3.connect("workspace.db")
            c = conn.cursor()
            c.execute("SELECT name, time_logged FROM team_members")
            team = [{"name": row[0], "time_logged": row[1]} for row in c.fetchall()]
            conn.close()
            self.write_json(200, team)
        except Exception as e:
            self.write_json(500, {"error": str(e)})

    def handle_team_post(self):
        try:
            payload = self.read_json()
            conn = sqlite3.connect("workspace.db")
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO team_members (name, time_logged) VALUES (?, 0)", (payload.get("name"),))
            conn.commit()
            conn.close()
            self.write_json(200, {"status": "ok"})
        except Exception as e:
            self.write_json(500, {"error": str(e)})

    def handle_team_delete(self, name):
        try:
            import urllib.parse
            name = urllib.parse.unquote(name)
            conn = sqlite3.connect("workspace.db")
            c = conn.cursor()
            c.execute("DELETE FROM team_members WHERE name = ?", (name,))
            conn.commit()
            conn.close()
            self.write_json(200, {"status": "ok"})
        except Exception as e:
            self.write_json(500, {"error": str(e)})

    def handle_team_time_post(self):
        try:
            payload = self.read_json()
            name = payload.get("name")
            time_logged = payload.get("time_logged")
            conn = sqlite3.connect("workspace.db")
            c = conn.cursor()
            c.execute("UPDATE team_members SET time_logged = ? WHERE name = ?", (time_logged, name))
            conn.commit()
            conn.close()
            self.write_json(200, {"status": "ok"})
        except Exception as e:
            self.write_json(500, {"error": str(e)})

    def handle_label_studio_send(self):
        try:
            payload = self.read_json()
            response = self.send_to_label_studio(payload)
            self.write_json(200, response)
        except (ValueError, DetectionClientError) as error:
            self.write_json(400, {"error": str(error)})
        except Exception:
            self.write_json(500, {"error": "Label Studio sync failed."})

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("Missing request body.")
        if length > MAX_BODY_BYTES:
            raise DetectionClientError(
                f"Request body exceeds {MAX_BODY_BYTES // (1024 * 1024)} MB limit."
            )
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def write_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_to_label_studio(self, payload):
        if not LABEL_STUDIO_API_KEY:
            raise ValueError("Set LABEL_STUDIO_API_KEY before starting server.py.")

        project_id = payload.get("projectId")
        task_id = payload.get("taskId")
        task_data = payload.get("taskData")
        result = payload.get("result")

        if not task_id and not project_id:
            raise ValueError("Send projectId to create a task, or taskId to annotate an existing task.")
        if not task_data:
            raise ValueError("Missing taskData.")
        if not result:
            raise ValueError("Missing annotation result.")

        client = LabelStudio(
            base_url=LABEL_STUDIO_URL,
            api_key=LABEL_STUDIO_API_KEY,
        )

        if not task_id:
            task = client.tasks.create(data=task_data, project=int(project_id))
            task_id = task.id

        annotation = client.annotations.create(
            int(task_id),
            result=result,
            was_cancelled=False,
            ground_truth=False,
        )

        return {
            "taskId": task_id,
            "annotationId": annotation.id,
            "labelStudioUrl": LABEL_STUDIO_URL,
        }


def init_db():
    conn = sqlite3.connect("workspace.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS workspace_data (key TEXT PRIMARY KEY, value TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, description TEXT, assignee TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS team_members (name TEXT PRIMARY KEY, time_logged INTEGER)''')
    conn.commit()
    conn.close()


def main():
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), AnnotationServer)
    print(f"App running at http://{HOST}:{PORT}/")
    print(f"Label Studio target: {LABEL_STUDIO_URL}")
    print("Object detection: YOLOv8 ONNX via OpenCV DNN")
    server.serve_forever()


if __name__ == "__main__":
    main()
