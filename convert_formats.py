#!/usr/bin/env python3
"""
Helper script to convert between different annotation formats.

Converts from various formats into JSON that can be imported using
import_annotations_manual.py

Usage:
    # Convert COCO to JSON
    python convert_formats.py --from coco --input coco.json --output annotations.json

    # Convert YOLO to JSON
    python convert_formats.py --from yolo --input labels/ --output annotations.json --image-width 1280 --image-height 720

    # Convert from CSV to JSON
    python convert_formats.py --from csv --input data.csv --output annotations.json
"""
import argparse
import json
import csv
from pathlib import Path
from typing import List, Dict, Any


def coco_to_json(coco_file: str, output_file: str) -> None:
    """Convert COCO JSON to import JSON format."""
    print(f"Converting COCO format: {coco_file} -> {output_file}")

    with open(coco_file, 'r') as f:
        coco_data = json.load(f)

    # Build category map
    categories = {cat['id']: cat['name'] for cat in coco_data.get('categories', [])}

    # Build image map
    images = {img['id']: img['file_name'] for img in coco_data.get('images', [])}

    # Group annotations by image
    annotations_by_image = {}

    for ann in coco_data.get('annotations', []):
        image_id = ann.get('image_id')
        image_name = images.get(image_id, f'image_{image_id}')

        if image_name not in annotations_by_image:
            annotations_by_image[image_name] = []

        # Convert bbox
        bbox = ann.get('bbox', [])
        category_id = ann.get('category_id', 0)
        label = categories.get(category_id, 'unknown')

        if bbox:
            annotations_by_image[image_name].append({
                'type': 'box',
                'label': label,
                'x': float(bbox[0]),
                'y': float(bbox[1]),
                'width': float(bbox[2]),
                'height': float(bbox[3])
            })

        # Convert segmentation
        segmentation = ann.get('segmentation', [])
        if segmentation and isinstance(segmentation, list) and segmentation[0]:
            seg_coords = segmentation[0]
            points = []
            for i in range(0, len(seg_coords), 2):
                if i + 1 < len(seg_coords):
                    points.append({
                        'x': seg_coords[i],
                        'y': seg_coords[i + 1]
                    })
            if points:
                annotations_by_image[image_name].append({
                    'type': 'polygon',
                    'label': label,
                    'points': points
                })

    # Save as JSON per-task (image name as key)
    with open(output_file, 'w') as f:
        json.dump(annotations_by_image, f, indent=2)

    print(f"✓ Converted {sum(len(a) for a in annotations_by_image.values())} annotations")
    print(f"✓ Saved to {output_file}")


def yolo_to_json(yolo_dir: str, output_file: str,
                 image_width: int = 1280, image_height: int = 720,
                 classes_file: str = None) -> None:
    """Convert YOLO labels to JSON format."""
    print(f"Converting YOLO format: {yolo_dir} -> {output_file}")

    yolo_path = Path(yolo_dir)

    # Load classes if provided
    classes = []
    if classes_file and Path(classes_file).exists():
        with open(classes_file, 'r') as f:
            classes = [line.strip() for line in f.readlines()]

    annotations_by_image = {}

    for txt_file in yolo_path.glob("*.txt"):
        image_name = f"{txt_file.stem}.jpg"

        annotations_by_image[image_name] = []

        with open(txt_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                parts = line.split()
                if len(parts) < 5:
                    continue

                class_id = int(parts[0])
                x_center = float(parts[1])
                y_center = float(parts[2])
                width = float(parts[3])
                height = float(parts[4])

                # Convert normalized to pixel coordinates
                x = (x_center - width / 2) * image_width
                y = (y_center - height / 2) * image_height
                w = width * image_width
                h = height * image_height

                # Get label
                if class_id < len(classes):
                    label = classes[class_id]
                else:
                    label = f"class_{class_id}"

                annotations_by_image[image_name].append({
                    'type': 'box',
                    'label': label,
                    'x': float(x),
                    'y': float(y),
                    'width': float(w),
                    'height': float(h)
                })

    # Save as JSON
    with open(output_file, 'w') as f:
        json.dump(annotations_by_image, f, indent=2)

    total = sum(len(a) for a in annotations_by_image.values())
    print(f"✓ Converted {total} annotations from {len(annotations_by_image)} images")
    print(f"✓ Saved to {output_file}")


def csv_to_json(csv_file: str, output_file: str, task_id: int = None) -> None:
    """Convert CSV to JSON format."""
    print(f"Converting CSV format: {csv_file} -> {output_file}")

    annotations = []

    with open(csv_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ann = {}

            # Required fields
            if 'type' in row:
                ann['type'] = row['type'].strip()
            if 'label' in row:
                ann['label'] = row['label'].strip()

            # Optional fields
            if 'x' in row and row['x'].strip():
                ann['x'] = float(row['x'])
            if 'y' in row and row['y'].strip():
                ann['y'] = float(row['y'])
            if 'width' in row and row['width'].strip():
                ann['width'] = float(row['width'])
            if 'height' in row and row['height'].strip():
                ann['height'] = float(row['height'])
            if 'text' in row and row['text'].strip():
                ann['text'] = row['text'].strip()
            if 'color' in row and row['color'].strip():
                ann['color'] = row['color'].strip()

            # Parse points for polygons
            if 'points' in row and row['points'].strip():
                try:
                    ann['points'] = json.loads(row['points'])
                except json.JSONDecodeError:
                    print(f"  Warning: Could not parse points: {row['points']}")

            if ann:
                annotations.append(ann)

    # Save as JSON
    if task_id:
        output_data = {str(task_id): annotations}
    else:
        output_data = annotations

    with open(output_file, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"✓ Converted {len(annotations)} annotations")
    print(f"✓ Saved to {output_file}")


def json_to_csv(json_file: str, output_file: str) -> None:
    """Convert JSON to CSV format."""
    print(f"Converting JSON format: {json_file} -> {output_file}")

    with open(json_file, 'r') as f:
        data = json.load(f)

    # Flatten if per-task
    annotations = []
    if isinstance(data, dict):
        # Per-task format
        for task_id, anns in data.items():
            annotations.extend(anns)
    else:
        # Array format
        annotations = data

    # Write CSV
    fieldnames = ['type', 'label', 'x', 'y', 'width', 'height', 'points', 'text', 'color']

    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for ann in annotations:
            row = {}
            for field in fieldnames:
                if field in ann:
                    value = ann[field]
                    if isinstance(value, (list, dict)):
                        row[field] = json.dumps(value)
                    else:
                        row[field] = value
                else:
                    row[field] = ''
            writer.writerow(row)

    print(f"✓ Converted {len(annotations)} annotations")
    print(f"✓ Saved to {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description='Convert between annotation formats'
    )
    parser.add_argument('--from', dest='from_format', required=True,
                       choices=['coco', 'yolo', 'csv', 'json'],
                       help='Input format')
    parser.add_argument('--to', dest='to_format', default='json',
                       choices=['json', 'csv'],
                       help='Output format (default: json)')
    parser.add_argument('--input', required=True,
                       help='Input file or directory')
    parser.add_argument('--output', required=True,
                       help='Output file')
    parser.add_argument('--task-id', type=int,
                       help='Task ID for CSV output (optional)')
    parser.add_argument('--image-width', type=int, default=1280,
                       help='Image width for YOLO conversion (default: 1280)')
    parser.add_argument('--image-height', type=int, default=720,
                       help='Image height for YOLO conversion (default: 720)')
    parser.add_argument('--classes',
                       help='Classes file for YOLO conversion (one class per line)')

    args = parser.parse_args()

    if args.from_format == 'coco':
        if args.to_format != 'json':
            print("Error: COCO can only be converted to JSON")
            return
        coco_to_json(args.input, args.output)

    elif args.from_format == 'yolo':
        if args.to_format != 'json':
            print("Error: YOLO can only be converted to JSON")
            return
        yolo_to_json(args.input, args.output,
                    args.image_width, args.image_height, args.classes)

    elif args.from_format == 'csv':
        if args.to_format == 'json':
            csv_to_json(args.input, args.output, args.task_id)
        elif args.to_format == 'csv':
            print("Error: CSV to CSV conversion not needed")
            return

    elif args.from_format == 'json':
        if args.to_format == 'csv':
            json_to_csv(args.input, args.output)
        elif args.to_format == 'json':
            print("Error: JSON to JSON conversion not needed")
            return

    print("\n✓ Conversion complete!")
    print(f"✓ Output saved to: {args.output}")


if __name__ == '__main__':
    main()
