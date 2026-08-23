"""Analyze all backup dumps to find the one with most annotations."""
import os
import re
from pathlib import Path

backup_dir = r"C:\annot-backups"
dumps = sorted(Path(backup_dir).glob("*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)

print(f"Analyzing {len(dumps)} dump files for annotation counts...\n")
print(f"{'Dump File':<40} {'Annotations':<15} {'Size (MB)':<12} {'Date/Time'}")
print("-" * 80)

results = []

for dump_file in dumps:
    try:
        # Get file size
        size_mb = dump_file.stat().st_size / (1024 * 1024)

        # Read file and count annotations
        with open(dump_file, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # Find COPY annotations section
        copy_start = content.find("COPY public.annotations")
        if copy_start == -1:
            annotation_count = 0
        else:
            copy_end = content.find("\n\\.\n", copy_start)
            if copy_end == -1:
                copy_end = content.find("\\.\n", copy_start)

            copy_section = content[copy_start:copy_end + 10]

            # Count data lines (excluding header and terminator)
            data_start = copy_section.find('\n') + 1
            data_end = copy_section.find('\\.', data_start)
            data_lines = copy_section[data_start:data_end].strip().split('\n')

            # Count non-empty lines that aren't metadata
            annotation_count = sum(1 for line in data_lines if line.strip() and not line.startswith('\\'))

        results.append({
            'file': dump_file.name,
            'path': dump_file,
            'count': annotation_count,
            'size_mb': size_mb,
            'time': dump_file.stat().st_mtime
        })

        print(f"{dump_file.name:<40} {annotation_count:<15} {size_mb:<12.2f}")

    except Exception as e:
        print(f"{dump_file.name:<40} ERROR: {e}")

# Sort by annotation count
results.sort(key=lambda x: x['count'], reverse=True)

print("\n" + "=" * 80)
print("\n🏆 BEST DUMP FILE (Most Annotations):\n")
if results:
    best = results[0]
    print(f"  File: {best['file']}")
    print(f"  Annotations: {best['count']}")
    print(f"  Size: {best['size_mb']:.2f} MB")
    print(f"\n✓ Recommended for restore: {best['path']}")

    # Check if task 243 has annotations in this best dump
    print(f"\n\nChecking if task 243 has annotations in best dump...")
    with open(best['path'], 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    copy_start = content.find("COPY public.annotations")
    if copy_start != -1:
        copy_end = content.find("\n\\.\n", copy_start)
        if copy_end == -1:
            copy_end = content.find("\\.\n", copy_start)

        copy_section = content[copy_start:copy_end + 10]
        data_start = copy_section.find('\n') + 1
        data_end = copy_section.find('\\.', data_start)
        data_lines = copy_section[data_start:data_end].strip().split('\n')

        task_243_lines = [line for line in data_lines if '\t243\t' in line or line.startswith('243\t')]
        if task_243_lines:
            print(f"✓ Task 243 has {len(task_243_lines)} annotations in this dump")
        else:
            print(f"✗ Task 243 has NO annotations even in the best dump")
else:
    print("No dumps found!")
