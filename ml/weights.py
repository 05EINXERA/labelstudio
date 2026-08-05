import os
import shutil
import urllib.request

from .common import DETECTOR_MODEL_DIR


def resolve_model_path(name, model_dir):
    """Resolve a model reference to a concrete path under model_dir.

    Hugging Face repo ids ("facebook/sam2-hiera-large") are passed through
    untouched so transformers can resolve them from the hub. Bare filenames are
    anchored to model_dir, otherwise Ultralytics resolves them against the
    current working directory and silently re-downloads into the repo root.
    """
    if not name:
        return name
    if os.path.isabs(name):
        return name
    if "/" in name or "\\" in name:
        return name

    os.makedirs(model_dir, exist_ok=True)
    return os.path.join(model_dir, name)


def ensure_model_file(model_size='n'):
    """Ensure YOLO ONNX model weights exist locally, downloading and exporting if necessary."""
    file_name = f'yolov8{model_size}-seg.onnx'
    download_url = f'https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8{model_size}-seg.pt'
    model_path = os.path.join(DETECTOR_MODEL_DIR, file_name)

    if os.path.isfile(model_path):
        return model_path

    model_dir = os.path.dirname(model_path) or "."
    os.makedirs(model_dir, exist_ok=True)

    is_pt_url = download_url.endswith(".pt")
    download_path = model_path.replace(".onnx", ".pt") if is_pt_url else model_path

    if not os.path.isfile(download_path):
        request = urllib.request.Request(
            download_url,
            headers={"User-Agent": "labelstudio-annotation-mvp/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                with open(download_path, "wb") as handle:
                    shutil.copyfileobj(response, handle)
        except Exception as error:
            raise RuntimeError(
                f"Could not download YOLO model from {download_url} to {download_path}. "
                "Set YOLO_download_url or place the model file in the models folder."
            ) from error

    if is_pt_url:
        try:
            from ultralytics import YOLO
            print(f"Exporting {download_path} to ONNX format...")
            model = YOLO(download_path)
            model.export(format="onnx")

            # Ultralytics saves the exported model in the same directory as the .pt file
            exported_path = download_path.replace(".pt", ".onnx")
            if exported_path != model_path and os.path.isfile(exported_path):
                shutil.move(exported_path, model_path)
        except ImportError:
            raise RuntimeError(
                f"Model downloaded as PyTorch (.pt) to {download_path}, but OpenCV requires ONNX (.onnx).\n"
                "Please install ultralytics to automatically convert it:\n"
                "  pip install ultralytics\n"
                "Or manually run:\n"
                f"  yolo export model={download_path} format=onnx\n"
                f"And ensure the resulting .onnx file is at {model_path}"
            )

    return model_path
