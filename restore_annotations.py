"""Restore annotations for task 243 from backup dump."""
import os
import re
import sys

os.environ['DATABASE_URL'] = 'postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation'

from sqlalchemy import create_engine, text
import json

# Read the dump file to extract annotations
dump_file = r"C:\annot-backups\workspace-20260821-162527.dump"

print(f"Reading backup dump: {dump_file}")
print("Extracting annotations for task 243...")

# Extract COPY data for annotations table
with open(dump_file, 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

# Find the COPY annotations section
copy_start = content.find("COPY public.annotations")
if copy_start == -1:
    print("ERROR: Could not find annotations table in dump file")
    sys.exit(1)

copy_end = content.find("\n\\.\n", copy_start)
if copy_end == -1:
    copy_end = content.find("\\.\n", copy_start)

copy_section = content[copy_start:copy_end + 10]

# Parse the COPY statement to get column names
copy_match = re.search(r'COPY public\.annotations \((.*?)\) FROM stdin', copy_section)
if not copy_match:
    print("ERROR: Could not parse COPY statement")
    sys.exit(1)

columns = [col.strip() for col in copy_match.group(1).split(',')]
print(f"Columns: {columns}")

# Extract data lines and filter for task_id = 243
data_start = copy_section.find('\n', copy_match.end()) + 1
data_end = copy_section.find('\\.', data_start)
data_lines = copy_section[data_start:data_end].strip().split('\n')

annotations_to_restore = []
for line in data_lines:
    if not line.strip() or line.startswith('\\'):
        continue

    values = line.split('\t')
    if len(values) != len(columns):
        continue

    # Create a dict from columns and values
    row = dict(zip(columns, values))

    # Check if this is for task 243
    if row.get('task_id') == '243':
        annotations_to_restore.append(row)
        print(f"Found annotation: {row.get('id')} - type: {row.get('type')}")

print(f"\nTotal annotations found for task 243: {len(annotations_to_restore)}")

if not annotations_to_restore:
    print("No annotations found for task 243 in backup")
    sys.exit(0)

# Now restore to current database
print("\nRestoring to current database...")
engine = create_engine(os.environ['DATABASE_URL'])

with engine.connect() as conn:
    for ann in annotations_to_restore:
        # Prepare INSERT statement
        cols = []
        vals = []
        params = {}

        for col, val in ann.items():
            if val == '\\N':  # NULL in PostgreSQL dump
                continue
            cols.append(col)
            param_name = f"p_{col}"
            vals.append(f":{param_name}")
            params[param_name] = val

        sql = f"INSERT INTO annotations ({', '.join(cols)}) VALUES ({', '.join(vals)}) ON CONFLICT (id) DO NOTHING"
        try:
            conn.execute(text(sql), params)
        except Exception as e:
            print(f"  Error inserting annotation {ann.get('id')}: {e}")

    conn.commit()
    print(f"✓ Successfully restored {len(annotations_to_restore)} annotations for task 243")

# Verify
print("\nVerifying restore...")
with engine.connect() as conn:
    result = conn.execute(text('SELECT COUNT(*) FROM annotations WHERE task_id = 243'))
    count = result.scalar()
    print(f"✓ Current database has {count} annotations for task 243")
