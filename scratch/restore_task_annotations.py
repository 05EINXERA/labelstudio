import os
import sys
sys.path.insert(0, os.path.abspath("."))
import psycopg

db_restore = "postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation_restore"
db_live = "postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation"
task_id = 243

try:
    with psycopg.connect(db_restore) as conn_rest:
        with conn_rest.cursor() as cur_rest:
            cur_rest.execute("SELECT id, task_id, label_id, type, points, x, y, width, height, text, color, \"order\", group_id, extra, created_at FROM annotations WHERE task_id = %s", (task_id,))
            rows = cur_rest.fetchall()
            
            if not rows:
                print(f"No annotations found for task {task_id} in backup.")
                sys.exit(0)
            
            print(f"Found {len(rows)} annotations for task {task_id} in backup.")

    with psycopg.connect(db_live, autocommit=True) as conn_live:
        with conn_live.cursor() as cur_live:
            # First, delete existing annotations for this task in the live DB
            cur_live.execute("DELETE FROM annotations WHERE task_id = %s", (task_id,))
            print("Cleared existing annotations for this task in live db.")
            
            # Insert the restored annotations
            insert_query = """
                INSERT INTO annotations (id, task_id, label_id, type, points, x, y, width, height, text, color, "order", group_id, extra, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            for row in rows:
                cur_live.execute(insert_query, row)
            print(f"Successfully restored {len(rows)} annotations for task {task_id} in live db.")

except Exception as e:
    print(f"Error: {e}")
