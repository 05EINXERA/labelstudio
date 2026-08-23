# Quick Start: Import Annotations to Task 243

## The Situation
- Task 243 (ProjectId=42) has no annotations
- You have annotation data somewhere (JSON, CSV, COCO, or YOLO format)
- You want to restore those annotations to the task

## Step 1: Prepare Your Data

### Option A: Simple JSON (Recommended for Single Task)

Create a file named `my_annotations.json`:

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
    "type": "box",
    "label": "car",
    "x": 350,
    "y": 180,
    "width": 200,
    "height": 120
  }
]
```

### Option B: CSV (If you have a spreadsheet)

Create a file named `my_annotations.csv`:

```csv
type,label,x,y,width,height
box,person,100,200,150,250
box,car,350,180,200,120
polygon,building,"[[50,50],[400,50],[400,500],[50,500]]"
```

### Option C: Use Example Files

We provide example files you can copy and modify:
- `example_annotations.json` - JSON format example
- `example_annotations.csv` - CSV format example
- `example_annotations_per_task.json` - Multiple tasks example

## Step 2: Run the Import

### For JSON format:
```bash
cd c:\labelstudio
python import_annotations_manual.py \
  --format json \
  --file my_annotations.json \
  --task-id 243 \
  --project-id 42
```

### For CSV format:
```bash
cd c:\labelstudio
python import_annotations_manual.py \
  --format csv \
  --file my_annotations.csv \
  --task-id 243 \
  --project-id 42
```

## Step 3: Verify

You should see output like:
```
======================================================================
IMPORT SUMMARY
======================================================================
✓ Imported: 2
⚠ Skipped: 0

======================================================================
```

Then open the UI and go to Task 243 to see your annotations!

## Common Annotation Types

### Bounding Box
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
```json
{
  "type": "polygon",
  "label": "building",
  "points": [[50, 50], [400, 50], [400, 500], [50, 500]]
}
```

### Comment
```json
{
  "type": "comment",
  "label": "note",
  "text": "Check this",
  "x": 200,
  "y": 300
}
```

## Troubleshooting

**Q: Import says "Task 243 not found"**
- A: Make sure ProjectId is 42 and Task ID is 243

**Q: JSON file won't parse**
- A: Validate it: `python -m json.tool my_annotations.json`

**Q: No annotations appear after import**
- A: Refresh the browser. Check output says "✓ Imported: X"

**Q: What colors are available?**
- A: Use hex codes like `#e85d75`, `#0f8b8d`, `#f4a261` or omit for auto-assign

## Next Steps

Once you have annotations imported, you can:
1. Open the annotation canvas for task 243
2. Edit, add, or delete annotations
3. Change the task status (Completed, Approved, etc.)
4. Export the project with the new annotations

See `IMPORT_ANNOTATIONS_README.md` for advanced features and formats.
