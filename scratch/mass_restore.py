import os
import sys
import subprocess
import psycopg

db_restore = "postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation_restore"
db_live = "postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation"
pg_restore = r"C:\Program Files\PostgreSQL\18\bin\pg_restore.exe"
dump = r"C:\annot-backups\workspace-20260820-143810.dump"

env = os.environ.copy()
env["PGPASSWORD"] = "seinxera"

# 1. Create temporary database
print("Creating temporary database...")
with psycopg.connect("postgresql://seinxera05:seinxera@127.0.0.1:5435/postgres", autocommit=True) as conn:
    with conn.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS annotation_restore")
        cur.execute("CREATE DATABASE annotation_restore")

# 2. Restore the latest dump into the temporary database
print(f"Restoring {dump} into temporary database...")
subprocess.run([
    pg_restore,
    "--clean",
    "--if-exists",
    "--no-owner",
    "--no-privileges",
    "-t", "annotations",
    "-d", db_restore,
    dump
], check=False, env=env, capture_output=True)

# 3. Compare counts and restore
print("Comparing annotations counts...")
tasks_to_restore = []
total_restored_annotations = 0

try:
    with psycopg.connect(db_restore) as conn_rest, psycopg.connect(db_live, autocommit=True) as conn_live:
        with conn_rest.cursor() as cur_rest, conn_live.cursor() as cur_live:
            # Get backup counts per task
            cur_rest.execute("SELECT task_id, count(*) FROM annotations GROUP BY task_id")
            backup_counts = {row[0]: row[1] for row in cur_rest.fetchall()}
            
            # Get live counts per task
            cur_live.execute("SELECT task_id, count(*) FROM annotations GROUP BY task_id")
            live_counts = {row[0]: row[1] for row in cur_live.fetchall()}
            
            for task_id, backup_count in backup_counts.items():
                live_count = live_counts.get(task_id, 0)
                if live_count < backup_count:
                    tasks_to_restore.append(task_id)
                    print(f"Task {task_id}: Live has {live_count}, Backup has {backup_count}. Restoring...")
                    
                    # Fetch all annotations for this task from backup
                    cur_rest.execute("SELECT id, task_id, label_id, type, points, x, y, width, height, text, color, \"order\", group_id, extra, created_at FROM annotations WHERE task_id = %s", (task_id,))
                    rows = cur_rest.fetchall()
                    
                    # Delete live annotations and insert backup annotations
                    cur_live.execute("DELETE FROM annotations WHERE task_id = %s", (task_id,))
                    insert_query = """
                        INSERT INTO annotations (id, task_id, label_id, type, points, x, y, width, height, text, color, "order", group_id, extra, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    for row in rows:
                        cur_live.execute(insert_query, row)
                        
                    total_restored_annotations += len(rows)
                    
    print(f"\nSuccessfully restored {total_restored_annotations} annotations across {len(tasks_to_restore)} tasks.")

finally:
    # Cleanup temporary database
    print("Cleaning up temporary database...")
    with psycopg.connect("postgresql://seinxera05:seinxera@127.0.0.1:5435/postgres", autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP DATABASE IF EXISTS annotation_restore")

