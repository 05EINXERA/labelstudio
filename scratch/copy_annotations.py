import os
import sys
import json
sys.path.insert(0, os.path.abspath("."))
import psycopg
from psycopg import sql

db_restore = "postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation_restore"
db_live = "postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation"

try:
    with psycopg.connect(db_restore) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, annotations FROM tasks WHERE id = 243")
            row = cur.fetchone()
            if not row:
                print("Task 243 not found in backup.")
                sys.exit(1)
            task_id, annotations = row
            if annotations:
                if isinstance(annotations, str):
                    annotations = json.loads(annotations)
                print(f"Found annotations in backup for task 243: {len(annotations)} items.")
            else:
                print("Annotations are empty in backup for task 243.")
                sys.exit(1)

    # Now update the live database
    with psycopg.connect(db_live, autocommit=True) as conn_live:
        with conn_live.cursor() as cur_live:
            # We must convert to JSON string if it's a list/dict for psycopg insertion or use jsonb
            cur_live.execute(
                "UPDATE tasks SET annotations = %s WHERE id = 243",
                (json.dumps(annotations),)
            )
            print("Successfully updated live database for task 243.")
except Exception as e:
    print(f"Error: {e}")
