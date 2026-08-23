# Manual Annotation Import Tool

Import annotations from various formats into Label Studio.

## Overview

The `import_annotations_manual.py` script allows you to import annotations into tasks using:
- **JSON** - Single task annotation array
- **JSON (per-task)** - Multiple tasks in one file
- **CSV** - Comma-separated values
- **COCO** - COCO JSON format
- **YOLO** - YOLO label format

## Prerequisites

- PostgreSQL database running and configured in `.env`
- Task(s) already exist in the project
- Annotation data in one of the supported formats

## Supported Annotation Types

### Box (Bounding Box)
```json
{
  "type": "box",
  "label": "person",
  "x": 100,
  "y": 200,
  "width": 150,
  "height": 250,
  "color": "#e85d75"
}
```

### Polygon
```json
{
  "type": "polygon",
  "label": "building",
  "points": [
    {"x": 50, "y": 50},
    {"x": 400, "y": 50},
    {"x": 400, "y": 500},
    {"x": 50, "y": 500}
  ]
}
```

### Comment
```json
{
  "type": "comment",
  "label": "note",
  "text": "Important observation",
  "x": 200,
  "y": 300
}
```

## Usage Examples

### 1. JSON Format (Single Task)

**File: `annotations.json`**
```json
[
  {
    "type": "box",
    "label": "person",
    "x": 100,
    "y": 200,
    "width": 150,
    "height": 250
  },
  {
    "type": "polygon",
    "label": "car",
    "points": [[10, 20], [100, 20], [100, 150], [10, 150]]
  }
]
```

**Command:**
```bash
python import_annotations_manual.py \
  --format json \
  --file annotations.json \
  --task-id 243 \
  --project-id 42
```

### 2. JSON Per-Task Format (Multiple Tasks)

**File: `annotations_by_task.json`**
```json
{
  "243": [
    {
      "type": "box",
      "label": "person",
      "x": 100,
      "y": 200,
      "width": 150,
      "height": 250
    }
  ],
  "244": [
    {
      "type": "box",
      "label": "car",
      "x": 50,
      "y": 100,
      "width": 200,
      "height": 120
    }
  ]
}
```

**Command:**
```bash
python import_annotations_manual.py \
  --format json-per-task \
  --file annotations_by_task.json \
  --project-id 42
```

### 3. CSV Format

**File: `annotations.csv`**
```csv
type,label,x,y,width,height,text,color
box,person,100,200,150,250,,#e85d75
box,car,350,180,200,120,,#0f8b8d
polygon,building,"[[50,50],[400,50],[400,500],[50,500]]",,,,,#2a9d8f
comment,note,200,300,,,Important object,#7b2cbf
```

**Command:**
```bash
python import_annotations_manual.py \
  --format csv \
  --file annotations.csv \
  --task-id 243 \
  --project-id 42
```

### 4. COCO Format

**File: `coco_annotations.json`**
```json
{
  "images": [
    {"id": 1, "file_name": "P1000552.JPG", "width": 1280, "height": 720}
  ],
  "annotations": [
    {
      "id": 1,
      "image_id": 1,
      "category_id": 0,
      "bbox": [100, 200, 150, 250],
      "area": 37500,
      "iscrowd": 0
    },
    {
      "id": 2,
      "image_id": 1,
      "category_id": 1,
      "segmentation": [[50, 50, 400, 50, 400, 500, 50, 500]],
      "area": 157500,
      "iscrowd": 0
    }
  ],
  "categories": [
    {"id": 0, "name": "person"},
    {"id": 1, "name": "car"}
  ]
}
```

**Command:**
```bash
python import_annotations_manual.py \
  --format coco \
  --file coco_annotations.json \
  --project-id 42
```

### 5. YOLO Format

**Directory Structure:**
```
yolo_labels/
  P1000552.txt
  P1000553.txt
  P1000554.txt
```

**File: `yolo_labels/P1000552.txt`**
```
0 0.5 0.5 0.3 0.4
1 0.7 0.3 0.2 0.2
2 0.2 0.8 0.1 0.1
```

Format: `class_id center_x center_y width height` (normalized 0-1)

**Command:**
```bash
python import_annotations_manual.py \
  --format yolo \
  --dir yolo_labels/ \
  --project-id 42
```

## Required Parameters

| Parameter | Format | Description |
|-----------|--------|-------------|
| `--format` | All | One of: json, json-per-task, csv, coco, yolo |
| `--project-id` | All | Project ID (required) |
| `--file` | json, json-per-task, csv, coco | Path to import file |
| `--dir` | yolo | Path to YOLO labels directory |
| `--task-id` | json, csv | Task ID (required for single-task formats) |

## JSON Annotation Fields

### Common Fields
- `type` (required): "box", "polygon", or "comment"
- `label` (required): Class/label name
- `color` (optional): Hex color code (e.g., "#e85d75")
- `group_id` (optional): Group ID for grouped annotations

### Box-Specific Fields
- `x`, `y`: Top-left corner position
- `width`, `height`: Dimensions

### Polygon-Specific Fields
- `points`: Array of {x, y} coordinates
  ```json
  "points": [
    {"x": 10, "y": 20},
    {"x": 100, "y": 20},
    {"x": 100, "y": 150}
  ]
  ```

### Comment-Specific Fields
- `text`: Comment text
- `x`, `y`: Position on image

## CSV Format

### Column Headers
```csv
type, label, x, y, width, height, points, text, color
```

### Column Details
- `type`: "box", "polygon", or "comment"
- `label`: Class name
- `x`, `y`, `width`, `height`: For boxes
- `points`: For polygons (JSON array as string: "[[10,20],[100,20],[100,150]]")
- `text`: For comments
- `color`: Optional hex color

## Output

After import, the script displays:
```
======================================================================
IMPORT SUMMARY
======================================================================
✓ Imported: 25
⚠ Skipped: 2

❌ Errors (3):
  - Invalid YOLO line format
  - Task not found for image
  - Missing required field

======================================================================
```

## Troubleshooting

### Error: "Task not found"
- Verify task ID and project ID are correct
- Check task exists: `SELECT id FROM tasks WHERE id = 243 AND project_id = 42`

### Error: "Invalid JSON"
- Validate JSON syntax using: `python -m json.tool annotations.json`
- Ensure all quotes are properly escaped

### Error: "Missing required field"
- Check that all annotations have required fields (type, label)
- Review the format specification above

### Error: "Column X not found" (CSV)
- Ensure CSV has header row with column names
- Check for typos in column names

### Annotations not appearing
- Verify annotations were imported: Check IMPORT SUMMARY output
- Refresh browser or reload the task
- Check database directly: `SELECT COUNT(*) FROM annotations WHERE task_id = 243`

## Example Workflow

1. **Prepare data** - Create `annotations.json` with annotation data

2. **Validate task exists**:
   ```bash
   python -c "
   from sqlalchemy import create_engine, text
   import os
   engine = create_engine(os.environ['DATABASE_URL'])
   with engine.connect() as conn:
       result = conn.execute(text('SELECT id FROM tasks WHERE id = 243'))
       print(result.fetchone())
   "
   ```

3. **Run import**:
   ```bash
   python import_annotations_manual.py \
     --format json \
     --file annotations.json \
     --task-id 243 \
     --project-id 42
   ```

4. **Verify in UI**:
   - Open the annotation canvas for task 243
   - Check that annotations appear on the image
   - Verify labels and colors are correct

## Data Format Quick Reference

### Simple JSON (Single Task)
```
File: data.json
Content: [ {annotation1}, {annotation2}, ... ]
Command: --format json --file data.json --task-id 243
```

### Per-Task JSON
```
File: data.json
Content: { "243": [...], "244": [...], ... }
Command: --format json-per-task --file data.json
```

### CSV
```
File: data.csv
Columns: type, label, x, y, width, height, color
Command: --format csv --file data.csv --task-id 243
```

### COCO
```
File: coco.json
Content: {images, annotations, categories}
Command: --format coco --file coco.json
```

### YOLO
```
Dir: labels/
Files: P1000552.txt, P1000553.txt, ...
Command: --format yolo --dir labels/
```

## Notes

- **Labels are case-insensitive** and will be normalized to lowercase
- **New labels are auto-created** if they don't exist in the project
- **Polygon points** can be provided as JSON string or array
- **Colors** are optional; a default palette is used if not provided
- **Imports are transactional** - all or nothing per format
- **Duplicate IDs** are skipped (ON CONFLICT DO NOTHING)

## Support

For issues or questions, check:
- Example files: `example_annotations.json`, `example_annotations.csv`, `example_annotations_per_task.json`
- Database connection: Verify `.env` DATABASE_URL is correct
- Task existence: Query database directly to confirm task exists
