"""Train a YOLOv8 segmentation model on a dataset prepared from an app export.

    python scripts/prepare_yolo_dataset.py --export ~/Downloads/<project>.zip
    python scripts/train_yolo.py --data datasets/<project>

The app exports polygons (YOLO segmentation labels), so the default base model
is a -seg model. Training runs on the first CUDA GPU and refuses to fall back
to the CPU silently; pass --device cpu to opt in.
"""
import argparse
import os
import sys

try:
    import torch
    from ultralytics import YOLO
except ImportError:
    print("Error: 'torch' / 'ultralytics' are not installed in this Python environment.")
    print("Use the training venv: .venv-train\\Scripts\\python.exe scripts\\train_yolo.py ...")
    sys.exit(1)


def find_yaml(path):
    if os.path.isfile(path) and path.endswith(".yaml"):
        return path
    if os.path.isdir(path):
        for f in sorted(os.listdir(path)):
            if f.endswith(".yaml"):
                return os.path.join(path, f)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", required=True,
                        help="Dataset folder (or data.yaml) from scripts/prepare_yolo_dataset.py")
    parser.add_argument("--model", default="yolov8n-seg.pt",
                        help="Base model (default: yolov8n-seg.pt; 4 GB GPUs suit n/s)")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8,
                        help="Batch size (default: 8). Use -1 for auto-sizing to GPU memory.")
    parser.add_argument("--workers", type=int, default=4, help="Dataloader workers (default: 4)")
    parser.add_argument("--device", default="0", help="'0' (first GPU, default) or 'cpu'")
    parser.add_argument("--name", default="custom_yolo", help="Run name under training_runs/")
    args = parser.parse_args()

    yaml_file = find_yaml(args.data)
    if not yaml_file:
        print(f"Error: no .yaml found at '{args.data}'. Run scripts/prepare_yolo_dataset.py first.")
        sys.exit(1)

    if args.device != "cpu" and not torch.cuda.is_available():
        print("Error: CUDA is not available to this PyTorch build "
              f"(torch {torch.__version__}). Install a CUDA wheel, or pass --device cpu.")
        sys.exit(1)

    gpu = torch.cuda.get_device_name(int(args.device)) if args.device != "cpu" else "CPU"
    print(f"--- YOLO training on {gpu} ---")
    print(f"Model {args.model} | data {yaml_file} | epochs {args.epochs} | imgsz {args.imgsz} | batch {args.batch}")

    model = YOLO(args.model)
    model.train(
        data=yaml_file,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        workers=args.workers,
        device=args.device,
        project="training_runs",
        name=args.name,
        exist_ok=True,
    )

    print("\n=== Training Complete ===")
    print(f"Weights: training_runs/{args.name}/weights/best.pt")
    print("Note: the app's detector (ml/yolo.py) is hardcoded to the 80 COCO classes and")
    print("yolov8<size>-seg.onnx, so these weights cannot be dropped into models/ as-is.")


if __name__ == "__main__":
    main()
