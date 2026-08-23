"""Search all backup dumps for annotations of task 243."""
import os
import re
from pathlib import Path

backup_dir = r"C:\annot-backups"
dumps = sorted(Path(backup_dir).glob("*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)

print(f"Searching {len(dumps)} dump files for task 243 annotations...")

found_annotations = []

for dump_file in dumps[:5]:  # Check last 5 backups
    print(f"\nChecking {dump_file.name}...")

    try:
        with open(dump_file, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # Find COPY annotations section
        copy_start = content.find("COPY public.annotations")
        if copy_start == -1:
            print("  (no annotations table)")
            continue

        copy_end = content.find("\n\\.\n", copy_start)
        if copy_end == -1:
            copy_end = content.find("\\.\n", copy_start)

        copy_section = content[copy_start:copy_end + 10]

        # Find all lines with task_id=243
        data_start = copy_section.find('\n') + 1
        data_end = copy_section.find('\\.', data_start)
        data_lines = copy_section[data_start:data_end].strip().split('\n')

        task_243_lines = [line for line in data_lines if '\t243\t' in line or line.startswith('243\t')]

        if task_243_lines:
            print(f"  ✓ Found {len(task_243_lines)} annotations for task 243")
            found_annotations.extend((dump_file.name, line) for line in task_243_lines)

    except Exception as e:
        print(f"  Error: {e}")

if found_annotations:
    print(f"\n\nTotal found: {len(found_annotations)} annotations")
    print("\nAnnotation data:")
    for dump_name, line in found_annotations:
        print(f"{dump_name}: {line[:150]}...")
else:
    print("\n\n✗ No annotations found for task 243 in any backup dump")
    print("\nPossible reasons:")
    print("1. Task 243 was created after backups started")
    print("2. The task never had any annotations added")
    print("3. Annotations were deleted")
