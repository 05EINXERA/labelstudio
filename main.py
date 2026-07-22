import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from database import engine, Base
from api.routers import projects, tasks, team, data, detect, label_studio, labels, auth, imports, exports
from config import DATA_DIR

HOST = "127.0.0.1"
PORT = int(os.environ.get("APP_PORT", "8765"))

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI()

@app.middleware("http")
async def add_cache_headers(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if (
        path.endswith(".js") or
        path.endswith(".html") or
        path.endswith(".css") or
        path == "/" or
        path.startswith("/frontend")
    ):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

_cors_origins = os.environ.get("CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins.split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(data.router)
app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(team.router)
app.include_router(detect.router)
app.include_router(label_studio.router)
app.include_router(labels.router)
app.include_router(auth.router)
app.include_router(imports.router)
app.include_router(exports.router)

# Ensure uploads directory exists
uploads_dir = os.path.join(DATA_DIR, "uploads")
os.makedirs(uploads_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

# Serve frontend static files
# Route the root URL to index.html
@app.get("/")
def read_index():
    return FileResponse("frontend/index.html")

# Mount the rest of the frontend directory
app.mount("/", StaticFiles(directory="frontend"), name="frontend")

if __name__ == "__main__":
    import uvicorn
    print(f"App running at http://{HOST}:{PORT}/")
    print("Object detection: YOLOv8 ONNX via OpenCV DNN")
    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
