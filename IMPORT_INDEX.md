# Annotation Import System - Index

## Overview

Complete system for importing annotations into Label Studio for task 243 (ProjectId=42) and other tasks.

**Status:** ✅ Ready to use

## Files

### Tools (Python Scripts)
1. **[import_annotations_manual.py](./import_annotations_manual.py)**
   - Main annotation import tool
   - Supports: JSON, CSV, COCO, YOLO formats
   - Single-task and multi-task imports
   - Usage: `python import_annotations_manual.py --format json --file data.json --task-id 243 --project-id 42`

2. **[convert_formats.py](./convert_formats.py)**
   - Convert between annotation formats
   - COCO → JSON, YOLO → JSON, CSV ↔ JSON
   - Usage: `python convert_formats.py --from coco --input coco.json --output data.json`

### Example Data
1. **[example_annotations.json](./example_annotations.json)**
   - Simple JSON format example
   - Single task, multiple annotation types
   - Ready to copy and customize

2. **[example_annotations.csv](./example_annotations.csv)**
   - CSV format example
   - Spreadsheet-compatible format
   - Ready to edit in Excel or Google Sheets

3. **[example_annotations_per_task.json](./example_annotations_per_task.json)**
   - Multi-task JSON format
   - Import annotations for multiple tasks at once
   - Useful for batch operations

### Documentation

#### Quick References
- **[IMPORT_QUICK_START.md](./IMPORT_QUICK_START.md)** ⭐ START HERE
  - 3-step quick start
  - Minimal setup needed
  - Examples for common use cases

#### Complete References
- **[IMPORT_ANNOTATIONS_README.md](./IMPORT_ANNOTATIONS_README.md)**
  - Full documentation
  - All supported formats explained
  - Usage examples for each format
  - Troubleshooting guide

- **[ANNOTATION_IMPORT_GUIDE.md](./ANNOTATION_IMPORT_GUIDE.md)**
  - Comprehensive workflow guide
  - Step-by-step instructions
  - Advanced usage examples
  - Performance notes

#### Summary
- **[ANNOTATION_IMPORT_SUMMARY.txt](./ANNOTATION_IMPORT_SUMMARY.txt)**
  - Quick reference checklist
  - File listing
  - Common issues & solutions

## Quick Start (3 Steps)

### Step 1: Prepare Data
```json
// File: annotations.json
[
  {
    "type": "box",
    "label": "person",
    "x": 100,
    "y": 200,
    "width": 150,
    "height": 250
  }
]
```

### Step 2: Run Import
```bash
python import_annotations_manual.py \
  --format json \
  --file annotations.json \
  --task-id 243 \
  --project-id 42
```

### Step 3: Verify
Open http://localhost:8000 and view task 243 in the annotation canvas

## Supported Formats

| Format | Command | File Type | Multi-Task | Example |
|--------|---------|-----------|-----------|---------|
| JSON | `--format json` | `.json` | ❌ Single | `example_annotations.json` |
| JSON (Per-Task) | `--format json-per-task` | `.json` | ✅ Yes | `example_annotations_per_task.json` |
| CSV | `--format csv` | `.csv` | ❌ Single | `example_annotations.csv` |
| COCO | `--format coco` | `.json` | ✅ Yes | See docs |
| YOLO | `--format yolo` | `/dir/` | ✅ Yes | See docs |

## Annotation Types Supported

- **Box** - Bounding box rectangles
- **Polygon** - Multi-point polygons
- **Comment** - Text annotations

## Common Commands

### Import single task JSON
```bash
python import_annotations_manual.py --format json --file data.json --task-id 243 --project-id 42
```

### Import multiple tasks JSON
```bash
python import_annotations_manual.py --format json-per-task --file data.json --project-id 42
```

### Import CSV
```bash
python import_annotations_manual.py --format csv --file data.csv --task-id 243 --project-id 42
```

### Import COCO
```bash
python import_annotations_manual.py --format coco --file coco.json --project-id 42
```

### Import YOLO
```bash
python import_annotations_manual.py --format yolo --dir labels/ --project-id 42
```

### Convert COCO to JSON
```bash
python convert_formats.py --from coco --input coco.json --output data.json
```

### Convert YOLO to JSON
```bash
python convert_formats.py --from yolo --input labels/ --output data.json
```

## Workflow

```
Your Data (JSON/CSV/COCO/YOLO)
       ↓
   (Optional) Convert to JSON using convert_formats.py
       ↓
   Import using import_annotations_manual.py
       ↓
   Verify in UI or database
       ↓
   Edit, export, or manage annotations
```

## Prerequisites

- PostgreSQL database running
- `.env` configured with DATABASE_URL
- Task already exists in project
- Python 3.7+ with SQLAlchemy installed

## Verification

After import, verify with:
```bash
# Check import summary output for "✓ Imported: X"

# Query database
SELECT COUNT(*) FROM annotations WHERE task_id = 243;

# Check in UI by opening task 243 in canvas
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Task not found" | Verify task ID and project ID are correct |
| "Invalid JSON" | Validate with `python -m json.tool file.json` |
| Annotations don't appear | Refresh browser, check import output shows success |
| Database connection failed | Check .env DATABASE_URL and PostgreSQL status |

## Next Steps

1. **Choose a format** - JSON recommended for simplicity
2. **Prepare your data** - Use example files as templates
3. **Run import** - Execute appropriate command
4. **Verify** - Check UI and/or database
5. **Manage** - Edit, export, or continue annotating

## Support Files

All documentation is in markdown format and can be read with:
- Any text editor
- GitHub web interface
- Markdown viewers

Example data files are ready to use and can be customized.

## Statistics

- **Files Created:** 9
- **Python Scripts:** 2 (valid syntax ✓)
- **Documentation:** 5 files
- **Example Data:** 3 files
- **Supported Formats:** 5
- **Annotation Types:** 3
- **Command Examples:** 15+

---

**Last Updated:** 2026-08-21
**Status:** Production Ready ✅
