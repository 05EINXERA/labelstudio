# Annotation Import Guide

Complete guide for importing annotations into Label Studio for task 243 and other tasks.

## Files Created

1. **`import_annotations_manual.py`** - Main import tool
   - Imports from JSON, CSV, COCO, YOLO formats
   - Supports single task and multi-task imports
   - Handles label creation automatically

2. **`convert_formats.py`** - Format converter
   - Convert COCO → JSON
   - Convert YOLO → JSON
   - Convert CSV ↔ JSON
   - Useful if you have data in different format

3. **Example Data Files**
   - `example_annotations.json` - Simple JSON format
   - `example_annotations.csv` - CSV format
   - `example_annotations_per_task.json` - Multi-task JSON

4. **Documentation**
   - `IMPORT_QUICK_START.md` - 3-step quick start
   - `IMPORT_ANNOTATIONS_README.md` - Complete reference
   - `ANNOTATION_IMPORT_GUIDE.md` - This file

## Quick Summary

### If you have annotations as JSON:
```bash
python import_annotations_manual.py \
  --format json \
  --file annotations.json \
  --task-id 243 \
  --project-id 42
```

### If you have annotations as CSV:
```bash
python import_annotations_manual.py \
  --format csv \
  --file annotations.csv \
  --task-id 243 \
  --project-id 42
```

### If you have COCO format:
```bash
python import_annotations_manual.py \
  --format coco \
  --file coco_data.json \
  --project-id 42
```

### If you have YOLO format:
```bash
python import_annotations_manual.py \
  --format yolo \
  --dir yolo_labels/ \
  --project-id 42
```

## Workflow

### Step 1: Prepare Your Data

**Option A: If you have JSON data**
- Create `my_annotations.json` with your annotations
- Proceed to Step 2

**Option B: If you have data in another format**
- Use `convert_formats.py` to convert first:
  ```bash
  python convert_formats.py \
    --from coco \
    --input coco.json \
    --output my_annotations.json
  ```
- Proceed to Step 2

**Option C: If you need to create data**
- Copy `example_annotations.json` to `my_annotations.json`
- Edit it with your annotation data
- Proceed to Step 2

### Step 2: Verify Task Exists

Before importing, verify the task exists:
```bash
python -c "
from sqlalchemy import create_engine, text
import os
engine = create_engine(os.environ['DATABASE_URL'])
with engine.connect() as conn:
    result = conn.execute(text('SELECT id, description FROM tasks WHERE id = 243 AND project_id = 42'))
    task = result.fetchone()
    if task:
        print(f'✓ Task found: {task}')
    else:
        print('✗ Task not found')
"
```

### Step 3: Import Annotations

Run the import command appropriate for your data format (see Quick Summary above).

### Step 4: Verify in UI

1. Open the annotation canvas for task 243
2. Check that annotations appear on the image
3. Verify labels and colors are correct

## Data Format Reference

### JSON Format (Single Task)

File: `annotations.json`
```json
[
  {
    "type": "box",
    "label": "person",
    "x": 100,
    "y": 200,
    "width": 150,
    "height": 250,
    "color": "#e85d75"
  },
  {
    "type": "polygon",
    "label": "car",
    "points": [
      {"x": 10, "y": 20},
      {"x": 100, "y": 20},
      {"x": 100, "y": 150},
      {"x": 10, "y": 150}
    ]
  },
  {
    "type": "comment",
    "text": "Important note",
    "x": 200,
    "y": 300
  }
]
```

Import with:
```bash
python import_annotations_manual.py --format json --file annotations.json --task-id 243 --project-id 42
```

### JSON Format (Multiple Tasks)

File: `annotations.json`
```json
{
  "243": [
    {"type": "box", "label": "person", "x": 100, "y": 200, "width": 150, "height": 250},
    {"type": "box", "label": "car", "x": 350, "y": 180, "width": 200, "height": 120}
  ],
  "244": [
    {"type": "box", "label": "cat", "x": 50, "y": 100, "width": 75, "height": 100}
  ],
  "245": [
    {"type": "comment", "text": "Check this", "x": 200, "y": 200}
  ]
}
```

Import with:
```bash
python import_annotations_manual.py --format json-per-task --file annotations.json --project-id 42
```

### CSV Format

File: `annotations.csv`
```csv
type,label,x,y,width,height,text,color
box,person,100,200,150,250,,#e85d75
box,car,350,180,200,120,,#0f8b8d
polygon,building,"[[50,50],[400,50],[400,500],[50,500]]",,,,,#f4a261
comment,note,200,300,,,Important,#2a9d8f
```

Import with:
```bash
python import_annotations_manual.py --format csv --file annotations.csv --task-id 243 --project-id 42
```

### COCO Format

File: `coco.json`
```json
{
  "images": [
    {"id": 1, "file_name": "P1000552.JPG"}
  ],
  "annotations": [
    {"id": 1, "image_id": 1, "category_id": 0, "bbox": [100, 200, 150, 250]},
    {"id": 2, "image_id": 1, "category_id": 1, "segmentation": [[50, 50, 400, 50, 400, 500, 50, 500]]}
  ],
  "categories": [
    {"id": 0, "name": "person"},
    {"id": 1, "name": "car"}
  ]
}
```

Import with:
```bash
python import_annotations_manual.py --format coco --file coco.json --project-id 42
```

### YOLO Format

Directory: `yolo_labels/`
- `P1000552.txt` (for image P1000552.JPG)
- `P1000553.txt`
- etc.

File content (format: `class_id center_x center_y width height`):
```
0 0.5 0.5 0.3 0.4
1 0.7 0.3 0.2 0.2
```

Import with:
```bash
python import_annotations_manual.py --format yolo --dir yolo_labels/ --project-id 42
```

## Annotation Types

### Bounding Box (Box)
- Rectangular annotation
- Required fields: `x`, `y`, `width`, `height`
```json
{
  "type": "box",
  "label": "person",
  "x": 100,
  "y": 200,
  "width": 150,
  "height": 250
}
```

### Polygon
- Multi-point polygon annotation
- Required fields: `points` (array of {x, y} objects)
```json
{
  "type": "polygon",
  "label": "building",
  "points": [
    {"x": 50, "y": 50},
    {"x": 400, "y": 50},
    {"x": 400, "y": 500}
  ]
}
```

### Comment
- Text annotation at a position
- Required fields: `text`, `x`, `y`
```json
{
  "type": "comment",
  "text": "Important object",
  "x": 200,
  "y": 300
}
```

## Common Operations

### Import JSON and verify count
```bash
python import_annotations_manual.py --format json --file data.json --task-id 243 --project-id 42
# Then query to verify:
python -c "
from sqlalchemy import create_engine, text
import os
engine = create_engine(os.environ['DATABASE_URL'])
with engine.connect() as conn:
    result = conn.execute(text('SELECT COUNT(*) FROM annotations WHERE task_id = 243'))
    print(f'Total annotations: {result.scalar()}')
"
```

### Convert COCO to JSON first, then import
```bash
# Convert
python convert_formats.py --from coco --input coco.json --output converted.json

# Import
python import_annotations_manual.py --format json --file converted.json --task-id 243 --project-id 42
```

### Import YOLO with custom image dimensions
```bash
python import_annotations_manual.py \
  --format yolo \
  --dir yolo_labels/ \
  --project-id 42 \
  --image-width 1920 \
  --image-height 1080
```

### Convert JSON to CSV for editing
```bash
python convert_formats.py --from json --to csv --input annotations.json --output annotations.csv

# Edit in spreadsheet app, then convert back
python convert_formats.py --from csv --to json --input annotations.csv --output edited.json --task-id 243
```

## Troubleshooting

### Task not found
```
Error: Task 243 not found in project 42
```
**Solution:** Verify task exists:
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

### Invalid JSON
```
Error: Failed to read JSON file: Expecting value
```
**Solution:** Validate JSON:
```bash
python -m json.tool my_annotations.json
```

### Annotations don't appear after import
- Check import output shows `✓ Imported: X`
- Refresh browser (Ctrl+Shift+R for hard refresh)
- Check task is loaded: `GET http://localhost:8000/api/tasks/243`
- Verify in database: `SELECT COUNT(*) FROM annotations WHERE task_id = 243`

### "Module not found" error
- Ensure you're in the labelstudio directory: `cd c:\labelstudio`
- Ensure venv is activated (you should see `(venv)` in prompt)
- Check DATABASE_URL is set in `.env`

### Wrong colors or labels
- Labels are case-insensitive (normalized to lowercase)
- Colors are optional; default palette is used if omitted
- Verify label names in your data match what you want

## Advanced Usage

### Import with custom colors
```json
[
  {
    "type": "box",
    "label": "person",
    "x": 100,
    "y": 200,
    "width": 150,
    "height": 250,
    "color": "#FF0000"
  }
]
```

### Import grouped annotations
```json
[
  {
    "type": "box",
    "label": "person",
    "x": 100,
    "y": 200,
    "width": 150,
    "height": 250,
    "group_id": "group_1"
  },
  {
    "type": "box",
    "label": "face",
    "x": 120,
    "y": 210,
    "width": 50,
    "height": 60,
    "group_id": "group_1"
  }
]
```

### Batch import multiple files
```bash
for file in annotations_*.json; do
  python import_annotations_manual.py --format json --file $file --task-id 243 --project-id 42
done
```

## Performance Notes

- **Small imports (< 100 annotations)**: < 1 second
- **Medium imports (100-1000)**: 1-5 seconds
- **Large imports (1000+)**: 5-30 seconds depending on system

For very large imports, consider:
1. Split into multiple files
2. Import in batches
3. Increase database connection pool: `DB_POOL_SIZE` in `.env`

## Next Steps

1. **Prepare your data** using one of the formats above
2. **Run the import** using `import_annotations_manual.py`
3. **Verify in UI** that annotations appear correctly
4. **Optionally edit** annotations using the canvas
5. **Export** the project with annotations

## Support

- For import issues: Check `IMPORT_ANNOTATIONS_README.md`
- For format questions: Check example files
- For database issues: Verify `.env` DATABASE_URL configuration
- For data conversion: Use `convert_formats.py`
