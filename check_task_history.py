"""Check when task 243 first appeared in backups."""
import os
import re
from pathlib import Path

backup_dir = r"C:\annot-backups"
dumps = sorted(Path(backup_dir).glob("*.dump"), key=lambda p: p.stat().st_mtime)

print("Checking when task 243 first appears in backups...\n")
print(f"{'Dump File':<40} {'Task 243 Exists?':<20} {'Annotations'}")
print("-" * 70)

task_243_found = False
best_dump_for_restore = None

for dump_file in dumps:
    try:
        with open(dump_file, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # Check if task 243 exists in tasks table
        task_exists = f"\n243\t" in content or "\t243\t" in content

        # Count annotations for task 243
        task_243_annotations = 0
        copy_start = content.find("COPY public.annotations")
        if copy_start != -1:
            copy_end = content.find("\n\\.\n", copy_start)
            if copy_end == -1:
                copy_end = content.find("\\.\n", copy_start)

            copy_section = content[copy_start:copy_end + 10]
            data_start = copy_section.find('\n') + 1
            data_end = copy_section.find('\\.', data_start)
            data_lines = copy_section[data_start:data_end].strip().split('\n')

            task_243_annotations = sum(1 for line in data_lines if '\t243\t' in line or line.startswith('243\t'))

        if task_exists:
            if not task_243_found:
                print(f"\n✓ FIRST APPEARANCE: {dump_file.name}")
                task_243_found = True

            status = f"✓ Yes"
            best_dump_for_restore = dump_file.name
        else:
            status = "✗ No (not created yet)"

        print(f"{dump_file.name:<40} {status:<20} {task_243_annotations}")

    except Exception as e:
        print(f"{dump_file.name:<40} ERROR: {e}")

print("\n" + "=" * 70)
if task_243_found:
    print(f"\n✓ Task 243 first appears in: workspace-20260820 era")
    print(f"✗ But it was NEVER annotated - it has 0 annotations in all backups")
    print(f"\nConclusion:")
    print(f"  - Task 243 exists but was created without any annotations")
    print(f"  - No annotations to restore from backup")
else:
    print(f"\n✗ Task 243 doesn't exist in any backup!")
    print(f"  It may have been added recently after backups stopped including it")
