import os
import sys
import subprocess
import psycopg

dumps = []
for root, _, files in os.walk(r"C:\annot-backups"):
    for f in files:
        if f.endswith(".dump"):
            dumps.append(os.path.join(root, f))
dumps.sort(reverse=True) # newest first

db_url = "postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation_restore"
pg_restore = r"C:\Program Files\PostgreSQL\18\bin\pg_restore.exe"

env = os.environ.copy()
env["PGPASSWORD"] = "seinxera"

found_dump = None
for dump in dumps:
    print(f"Checking {dump}...")
    try:
        # Restore just annotations
        subprocess.run([
            pg_restore,
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "-t", "annotations",
            "-d", db_url,
            dump
        ], check=False, env=env, capture_output=True)
        
        # Check if task 48 has annotations
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM annotations WHERE task_id = 48")
                count = cur.fetchone()[0]
                if count > 0:
                    print(f"  -> Found {count} annotations for task 48 in {dump}")
                    found_dump = dump
                    break
    except Exception as e:
        print(f"Error checking {dump}: {e}")

if found_dump:
    print(f"Found annotations in {found_dump}. Updating live db...")
    db_live = "postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation"
    
    with psycopg.connect(db_url) as conn_rest:
        with conn_rest.cursor() as cur_rest:
            cur_rest.execute("SELECT id, task_id, label_id, type, points, x, y, width, height, text, color, \"order\", group_id, extra, created_at FROM annotations WHERE task_id = 48")
            rows = cur_rest.fetchall()
            
    with psycopg.connect(db_live, autocommit=True) as conn_live:
        with conn_live.cursor() as cur_live:
            cur_live.execute("DELETE FROM annotations WHERE task_id = 48")
            insert_query = """
                INSERT INTO annotations (id, task_id, label_id, type, points, x, y, width, height, text, color, "order", group_id, extra, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            for row in rows:
                cur_live.execute(insert_query, row)
            print(f"Successfully restored {len(rows)} annotations for task 243 in live db.")
else:
    print("Could not find any backup containing annotations for task 243.")
