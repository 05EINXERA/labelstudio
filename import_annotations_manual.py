#!/usr/bin/env python3
"""
Manual Annotation Import Tool

Import annotations into Label Studio from various formats:
- JSON (single array or per-task)
- CSV
- COCO JSON format
- YOLO format

Usage:
    python import_annotations_manual.py --format json --file annotations.json --task-id 243 --project-id 42
    python import_annotations_manual.py --format coco --file coco.json --project-id 42
    python import_annotations_manual.py --format csv --file annotations.csv --task-id 243
    python import_annotations_manual.py --format yolo --dir yolo_labels/ --project-id 42
"""
import argparse
import json
import csv
import sys
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

os.environ['DATABASE_URL'] = 'postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation'

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import models
from database import Base, commit_with_retry

# Initialize database
engine = create_engine(os.environ['DATABASE_URL'])
Session = sessionmaker(bind=engine)


def generate_uuid() -> str:
    """Generate a UUID for annotations."""
    import uuid
    return str(uuid.uuid4())


class AnnotationImporter:
    """Handle importing annotations from various formats."""

    def __init__(self):
        self.session = Session()
        self.imported_count = 0
        self.skipped_count = 0
        self.errors = []

    def verify_task_exists(self, task_id: int, project_id: int) -> bool:
        """Check if task exists in project."""
        task = self.session.query(models.Task).filter(
            models.Task.id == task_id,
            models.Task.project_id == project_id
        ).first()
        if not task:
            self.errors.append(f"Task {task_id} not found in project {project_id}")
            return False
        return True

    def verify_project_exists(self, project_id: int) -> bool:
        """Check if project exists."""
        project = self.session.query(models.Project).filter(
            models.Project.id == project_id
        ).first()
        if not project:
            self.errors.append(f"Project {project_id} not found")
            return False
        return True

    def get_label_id(self, label_name: str, project_id: int) -> Optional[str]:
        """Get or create label ID for a class name."""
        label = self.session.query(models.Label).filter(
            models.Label.name == label_name.lower(),
            models.Label.project_id == project_id
        ).first()

        if not label:
            # Create new label
            label = models.Label(
                id=generate_uuid(),
                project_id=project_id,
                name=label_name.lower(),
                color=self._get_default_color()
            )
            self.session.add(label)
            self.session.commit()

        return label.id

    def _get_default_color(self) -> str:
        """Get a default color from palette."""
        colors = [
            "#0f8b8d", "#e85d75", "#f4a261", "#2a9d8f", "#7b2cbf",
            "#3f88c5", "#d95d39", "#65727f", "#8d6e63", "#4dabf7"
        ]
        return colors[self.imported_count % len(colors)]

    def import_json_array(self, file_path: str, task_id: int, project_id: int) -> None:
        """Import from JSON array format.

        Expected format:
        [
            {
                "type": "box",
                "label": "person",
                "x": 100, "y": 200, "width": 50, "height": 100,
                "color": "#ff0000"
            },
            {
                "type": "polygon",
                "label": "car",
                "points": [[10, 20], [30, 20], [30, 50], [10, 50]],
                "color": "#00ff00"
            }
        ]
        """
        print(f"\nImporting from JSON array: {file_path}")

        if not self.verify_task_exists(task_id, project_id):
            return

        try:
            with open(file_path, 'r') as f:
                annotations = json.load(f)

            if not isinstance(annotations, list):
                self.errors.append(f"Expected JSON array, got {type(annotations)}")
                return

            for ann_data in annotations:
                try:
                    ann = self._create_annotation_from_dict(
                        ann_data, task_id, project_id
                    )
                    if ann:
                        self.session.add(ann)
                        self.imported_count += 1
                except Exception as e:
                    self.errors.append(f"Error importing annotation: {e}")
                    self.skipped_count += 1

            commit_with_retry(self.session)
            print(f"✓ Imported {self.imported_count} annotations")

        except Exception as e:
            self.errors.append(f"Failed to read JSON file: {e}")

    def import_json_per_task(self, file_path: str, project_id: int) -> None:
        """Import from per-task JSON format.

        Expected format:
        {
            "243": [
                {"type": "box", "label": "person", "x": 10, "y": 20, ...},
                ...
            ],
            "244": [
                ...
            ]
        }
        """
        print(f"\nImporting from per-task JSON: {file_path}")

        if not self.verify_project_exists(project_id):
            return

        try:
            with open(file_path, 'r') as f:
                data = json.load(f)

            for task_id_str, annotations in data.items():
                try:
                    task_id = int(task_id_str)

                    if not self.verify_task_exists(task_id, project_id):
                        self.skipped_count += 1
                        continue

                    for ann_data in annotations:
                        try:
                            ann = self._create_annotation_from_dict(
                                ann_data, task_id, project_id
                            )
                            if ann:
                                self.session.add(ann)
                                self.imported_count += 1
                        except Exception as e:
                            self.errors.append(f"Error importing annotation for task {task_id}: {e}")
                            self.skipped_count += 1

                except Exception as e:
                    self.errors.append(f"Error processing task {task_id_str}: {e}")
                    self.skipped_count += 1

            commit_with_retry(self.session)
            print(f"✓ Imported {self.imported_count} annotations across multiple tasks")

        except Exception as e:
            self.errors.append(f"Failed to read JSON file: {e}")

    def import_csv(self, file_path: str, task_id: int, project_id: int) -> None:
        """Import from CSV format.

        Expected columns:
        type, label, x, y, width, height, points, text, color

        Example:
        type,label,x,y,width,height,color
        box,person,100,200,50,100,#ff0000
        polygon,car,"[[10,20],[30,20],[30,50],[10,50]]",,,#00ff00
        comment,note,150,250,,,#0000ff
        """
        print(f"\nImporting from CSV: {file_path}")

        if not self.verify_task_exists(task_id, project_id):
            return

        try:
            with open(file_path, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        ann = self._create_annotation_from_dict(
                            row, task_id, project_id
                        )
                        if ann:
                            self.session.add(ann)
                            self.imported_count += 1
                    except Exception as e:
                        self.errors.append(f"Error importing CSV row: {e}")
                        self.skipped_count += 1

            commit_with_retry(self.session)
            print(f"✓ Imported {self.imported_count} annotations from CSV")

        except Exception as e:
            self.errors.append(f"Failed to read CSV file: {e}")

    def import_coco(self, file_path: str, project_id: int) -> None:
        """Import from COCO JSON format.

        Expected COCO format with annotations, images, categories.
        """
        print(f"\nImporting from COCO format: {file_path}")

        if not self.verify_project_exists(project_id):
            return

        try:
            with open(file_path, 'r') as f:
                coco_data = json.load(f)

            # Build category map
            categories = {cat['id']: cat['name'] for cat in coco_data.get('categories', [])}

            # Build image -> task map
            image_to_task = {}
            images = self.session.query(models.Task).filter(
                models.Task.project_id == project_id
            ).all()
            for img in images:
                # Match by image name (e.g., P1000552.JPG)
                image_to_task[img.description] = img.id

            # Import annotations
            for ann_data in coco_data.get('annotations', []):
                try:
                    image_id = ann_data.get('image_id')
                    # Find corresponding task (simplified - assumes image_id maps to task)
                    task_id = list(image_to_task.values())[0] if image_to_task else None

                    if not task_id:
                        self.errors.append(f"Could not find task for image {image_id}")
                        self.skipped_count += 1
                        continue

                    category_id = ann_data.get('category_id')
                    label_name = categories.get(category_id, 'unknown')

                    # Handle bbox (COCO format: [x, y, width, height])
                    bbox = ann_data.get('bbox', [])
                    if bbox:
                        ann = models.Annotation(
                            id=generate_uuid(),
                            task_id=task_id,
                            label_id=self.get_label_id(label_name, project_id),
                            type='box',
                            x=float(bbox[0]),
                            y=float(bbox[1]),
                            width=float(bbox[2]),
                            height=float(bbox[3]),
                            created_at=datetime.now()
                        )
                        self.session.add(ann)
                        self.imported_count += 1

                    # Handle segmentation (polygons)
                    segmentation = ann_data.get('segmentation', [])
                    if segmentation and isinstance(segmentation, list) and segmentation[0]:
                        points = self._coco_segmentation_to_points(segmentation[0])
                        ann = models.Annotation(
                            id=generate_uuid(),
                            task_id=task_id,
                            label_id=self.get_label_id(label_name, project_id),
                            type='polygon',
                            points=json.dumps(points),
                            created_at=datetime.now()
                        )
                        self.session.add(ann)
                        self.imported_count += 1

                except Exception as e:
                    self.errors.append(f"Error importing COCO annotation: {e}")
                    self.skipped_count += 1

            commit_with_retry(self.session)
            print(f"✓ Imported {self.imported_count} annotations from COCO format")

        except Exception as e:
            self.errors.append(f"Failed to read COCO file: {e}")

    def import_yolo(self, dir_path: str, project_id: int) -> None:
        """Import from YOLO format.

        Expected directory structure:
        yolo_labels/
          P1000552.txt  (labels for image P1000552.JPG)
          P1000553.txt
          ...

        Each .txt file contains: class_id x_center y_center width height (normalized 0-1)
        """
        print(f"\nImporting from YOLO format: {dir_path}")

        if not self.verify_project_exists(project_id):
            return

        yolo_dir = Path(dir_path)
        if not yolo_dir.exists():
            self.errors.append(f"YOLO directory not found: {dir_path}")
            return

        # Get all tasks in project
        tasks = self.session.query(models.Task).filter(
            models.Task.project_id == project_id
        ).all()

        # Create image name -> task map
        img_name_to_task = {task.description: task for task in tasks}

        for txt_file in yolo_dir.glob("*.txt"):
            # Get image name from label filename (e.g., P1000552.txt -> P1000552.JPG)
            img_name = txt_file.stem.upper()
            img_name_with_ext = f"{img_name}.JPG"

            task = img_name_to_task.get(img_name_with_ext)
            if not task:
                self.errors.append(f"No task found for image {img_name_with_ext}")
                self.skipped_count += 1
                continue

            try:
                with open(txt_file, 'r') as f:
                    for line_idx, line in enumerate(f):
                        line = line.strip()
                        if not line:
                            continue

                        parts = line.split()
                        if len(parts) < 5:
                            self.errors.append(f"Invalid YOLO line in {txt_file.name}: {line}")
                            continue

                        class_id = parts[0]
                        x_center = float(parts[1])
                        y_center = float(parts[2])
                        width = float(parts[3])
                        height = float(parts[4])

                        # Get image dimensions
                        img_task = self.session.query(models.Task).filter(
                            models.Task.id == task.id
                        ).first()
                        img_width = img_task.width or 1280
                        img_height = img_task.height or 720

                        # Convert normalized to pixel coordinates
                        x = (x_center - width / 2) * img_width
                        y = (y_center - height / 2) * img_height
                        w = width * img_width
                        h = height * img_height

                        # Get label (assuming class_id is index in classes list)
                        label = self.session.query(models.Label).filter(
                            models.Label.project_id == project_id
                        ).offset(int(class_id)).first()

                        if not label:
                            label_name = f"class_{class_id}"
                            label_id = self.get_label_id(label_name, project_id)
                        else:
                            label_id = label.id

                        ann = models.Annotation(
                            id=generate_uuid(),
                            task_id=task.id,
                            label_id=label_id,
                            type='box',
                            x=max(0, x),
                            y=max(0, y),
                            width=w,
                            height=h,
                            created_at=datetime.now()
                        )
                        self.session.add(ann)
                        self.imported_count += 1

            except Exception as e:
                self.errors.append(f"Error processing YOLO file {txt_file.name}: {e}")
                self.skipped_count += 1

        commit_with_retry(self.session)
        print(f"✓ Imported {self.imported_count} annotations from YOLO format")

    def _create_annotation_from_dict(self, data: Dict[str, Any], task_id: int, project_id: int) -> Optional[models.Annotation]:
        """Create annotation object from dict."""
        ann_type = data.get('type', 'box').lower()
        label_name = data.get('label', 'unlabeled')

        if not label_name:
            return None

        ann = models.Annotation(
            id=generate_uuid(),
            task_id=task_id,
            label_id=self.get_label_id(label_name, project_id),
            type=ann_type,
            created_at=datetime.now()
        )

        # Handle different annotation types
        if ann_type == 'box':
            ann.x = float(data.get('x', 0))
            ann.y = float(data.get('y', 0))
            ann.width = float(data.get('width', 0))
            ann.height = float(data.get('height', 0))

        elif ann_type == 'polygon':
            points = data.get('points', [])
            if isinstance(points, str):
                points = json.loads(points)
            ann.points = json.dumps(points)

        elif ann_type == 'comment':
            ann.text = data.get('text', '')
            ann.x = float(data.get('x', 0))
            ann.y = float(data.get('y', 0))

        # Optional fields
        if 'color' in data:
            ann.color = data['color']
        if 'group_id' in data:
            ann.group_id = data['group_id']
        if 'text' in data and ann_type != 'comment':
            ann.text = data['text']

        return ann

    def _coco_segmentation_to_points(self, segmentation: List[float]) -> List[Dict[str, float]]:
        """Convert COCO segmentation format to points."""
        points = []
        for i in range(0, len(segmentation), 2):
            if i + 1 < len(segmentation):
                points.append({
                    'x': segmentation[i],
                    'y': segmentation[i + 1]
                })
        return points

    def print_summary(self) -> None:
        """Print import summary."""
        print("\n" + "=" * 70)
        print("IMPORT SUMMARY")
        print("=" * 70)
        print(f"✓ Imported: {self.imported_count}")
        print(f"⚠ Skipped: {self.skipped_count}")

        if self.errors:
            print(f"\n❌ Errors ({len(self.errors)}):")
            for error in self.errors[:10]:  # Show first 10 errors
                print(f"  - {error}")
            if len(self.errors) > 10:
                print(f"  ... and {len(self.errors) - 10} more errors")

        print("\n" + "=" * 70)

    def close(self) -> None:
        """Close database session."""
        self.session.close()


def main():
    parser = argparse.ArgumentParser(
        description='Import annotations into Label Studio from various formats'
    )
    parser.add_argument('--format', required=True,
                       choices=['json', 'json-per-task', 'csv', 'coco', 'yolo'],
                       help='Import format')
    parser.add_argument('--file', help='Path to import file (for json, csv, coco formats)')
    parser.add_argument('--dir', help='Path to directory (for yolo format)')
    parser.add_argument('--task-id', type=int, help='Task ID (for single-task formats)')
    parser.add_argument('--project-id', type=int, required=True, help='Project ID')

    args = parser.parse_args()

    importer = AnnotationImporter()

    try:
        if args.format == 'json':
            if not args.file or not args.task_id:
                print("Error: --file and --task-id required for json format")
                sys.exit(1)
            importer.import_json_array(args.file, args.task_id, args.project_id)

        elif args.format == 'json-per-task':
            if not args.file:
                print("Error: --file required for json-per-task format")
                sys.exit(1)
            importer.import_json_per_task(args.file, args.project_id)

        elif args.format == 'csv':
            if not args.file or not args.task_id:
                print("Error: --file and --task-id required for csv format")
                sys.exit(1)
            importer.import_csv(args.file, args.task_id, args.project_id)

        elif args.format == 'coco':
            if not args.file:
                print("Error: --file required for coco format")
                sys.exit(1)
            importer.import_coco(args.file, args.project_id)

        elif args.format == 'yolo':
            if not args.dir:
                print("Error: --dir required for yolo format")
                sys.exit(1)
            importer.import_yolo(args.dir, args.project_id)

        importer.print_summary()

    except Exception as e:
        print(f"Fatal error: {e}")
        importer.print_summary()
        sys.exit(1)
    finally:
        importer.close()


if __name__ == '__main__':
    main()
