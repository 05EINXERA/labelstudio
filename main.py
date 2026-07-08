import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from database import engine, Base
from api.routers import projects, tasks, team, data, detect, label_studio, labels

HOST = "127.0.0.1"
PORT = int(os.environ.get("APP_PORT", "8765"))

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

# Ensure uploads directory exists
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

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
