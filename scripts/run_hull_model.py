"""Run the hull-condition model on chosen photos from the terminal.

Uses the same code path as the app's Hull button (ml/semseg.py), so results
match. Writes, per photo, an overlay PNG and a predictions JSON (polygons in
source pixels) into --out.

    venv\\Scripts\\python.exe scripts\\run_hull_model.py C:\\photos\\a.jpg C:\\photos\\folder
    venv\\Scripts\\python.exe scripts\\run_hull_model.py C:\\photos --model models\\detector\\hull_semseg_v6.pt
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

import config
from ml.images import decode_image
from ml.semseg import _predict_maps, combine_class_maps, mask_to_predictions, semseg_weights_path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
# BGR colours by class index (0 = sound hull, not drawn).
COLOURS = [(0, 0, 0), (0, 0, 255), (0, 165, 255), (0, 255, 255), (255, 0, 255),
           (255, 128, 0), (0, 200, 0), (30, 60, 200), (200, 200, 0)]


def collect_images(paths):
    found = []
    for path in paths:
        if os.path.isdir(path):
            for name in sorted(os.listdir(path)):
                if os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS:
                    found.append(os.path.join(path, name))
        elif os.path.isfile(path):
            found.append(path)
        else:
            print(f"skip (not found): {path}")
    return found


def resolve_model(path):
    return path if os.path.isabs(path) else os.path.abspath(path)


def run_one(image_path, model_paths, out_dir):
    with open(image_path, "rb") as handle:
        image = decode_image(handle.read())
    results, scale = [], None
    for model_path in model_paths:
        class_map, confidence, classes, model_scale, _ = _predict_maps(model_path, image)
        results.append((class_map, confidence, classes))
        scale = scale or model_scale
    class_map, confidence, classes = results[0] if len(results) == 1 else combine_class_maps(results)
    predictions = mask_to_predictions(
        class_map, confidence, classes, scale, config.SEMSEG_MIN_AREA_PX, config.SEMSEG_MAX_POLYGONS,
        vertex_spacing=config.SEMSEG_VERTEX_SPACING_PX,
    )

    base = os.path.splitext(os.path.basename(image_path))[0]
    canvas = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    layer = canvas.copy()
    for prediction in predictions:
        class_id = next((i for i, c in enumerate(classes) if c["name"] == prediction["class"]), 0)
        colour = COLOURS[class_id % len(COLOURS)]
        polygon = np.array([[p["x"], p["y"]] for p in prediction["points"]], np.int32)
        cv2.fillPoly(layer, [polygon], colour)
        cv2.polylines(canvas, [polygon], True, colour, max(2, image.width // 800))
    overlay = cv2.addWeighted(layer, 0.4, canvas, 0.6, 0)
    cv2.imwrite(os.path.join(out_dir, f"{base}_overlay.jpg"), overlay)
    with open(os.path.join(out_dir, f"{base}.json"), "w", encoding="utf-8") as handle:
        json.dump({"image": image_path, "predictions": predictions}, handle)
    counts = {}
    for prediction in predictions:
        counts[prediction["class"]] = counts.get(prediction["class"], 0) + 1
    print(f"{os.path.basename(image_path)}: {len(predictions)} regions {counts}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="+", help="photo files and/or folders")
    parser.add_argument("--model", default=None, help="weights file (default: SEMSEG_MODEL from .env)")
    parser.add_argument("--extra", nargs="*", default=[], help="more weights that fill what the first leaves empty")
    parser.add_argument("--out", default="hull_output", help="output folder (default: hull_output)")
    args = parser.parse_args()

    model_paths = [resolve_model(args.model) if args.model else semseg_weights_path()]
    model_paths += [resolve_model(p) for p in args.extra]
    for path in model_paths:
        if not os.path.isfile(path):
            sys.exit(f"Weights not found: {path}")
    images = collect_images(args.images)
    if not images:
        sys.exit("No images found.")
    os.makedirs(args.out, exist_ok=True)
    print("models:", ", ".join(os.path.basename(p) for p in model_paths))
    for image_path in images:
        run_one(image_path, model_paths, args.out)
    print(f"done -> {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
