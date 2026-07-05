# Image Annotation MVP

A browser workspace for AI-assisted bounding-box annotations on images, with optional Label Studio sync.

## Setup

Install Python dependencies (first run downloads the YOLOv8 ONNX model):

```powershell
.\venv\Scripts\pip.exe install -r requirements.txt
```

## Run

Start the local server (serves the app and runs object detection):

```powershell
$env:LABEL_STUDIO_URL = "http://localhost:8000/"
$env:LABEL_STUDIO_API_KEY = "your-label-studio-token"
.\venv\Scripts\python.exe server.py
```

Then open `http://127.0.0.1:8765/`.

Optional detection settings:

- `YOLO_MODEL` — ONNX model filename or path (default `models/yolov8n.onnx`)
- `YOLO_MODEL_URL` — download URL if the model file is missing (default Hugging Face `yolov8n.onnx`)
- `YOLO_INPUT_SIZE` — inference size (default `640`)
- `DETECT_CONFIDENCE` — minimum score (default `0.35`)
- `DETECT_NMS` — non-max suppression threshold (default `0.45`)
- `DETECT_MAX` — max boxes per image (default `100`)
- `MAX_BODY_BYTES` — max POST body size (default `25` MB)
- `MAX_IMAGE_BYTES` — max decoded image size (default `20` MB)
- `MAX_IMAGE_PIXELS` — max image pixel count (default `25_000_000`)

## Features

- Load or drag-and-drop an image.
- **Auto-detect** objects on load using **YOLOv8 ONNX** with **OpenCV DNN**. Class names and boxes are created automatically — no manual label setup.
- Draw or adjust bounding boxes manually (manual boxes use the `Object` class).
- Move and resize selected boxes in Select mode.
- Send the loaded image and current boxes to Label Studio with the Label Studio API.
- Select, delete, clear, and undo annotations.
- Use keyboard shortcuts: `D` draw, `S` select, `Delete` remove selected, `Ctrl/Cmd+Z` undo, `Esc` deselect.
- Auto-save the workspace in local browser storage.
- Import and export COCO-style JSON with `[x, y, width, height]` boxes.

## Label Studio

Load an image, let detection run (or click **Auto-detect**), then fill in the Label Studio panel and click `Send annotations`.

Fields:

- Proxy URL: optional. Leave blank when using `server.py`.
- Project ID: required when creating a new task.
- Task ID: optional; when present, annotations are added to that existing task.
- `from_name` and `to_name`: must match your Label Studio labeling config. Defaults are `label` and `image`.

The browser posts to the local `/api/label-studio/send` proxy. Detection uses `/api/detect` on the same server. Your project config should include an image data field named `$image` and rectangle label names that match the detected classes (e.g. `person`, `car`, `dog`).
