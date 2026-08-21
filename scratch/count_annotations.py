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

print("Checking backups for task 48...")
for dump in dumps:
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
    
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM annotations WHERE task_id = 48")
            count = cur.fetchone()[0]
            print(f"{dump}: {count} annotations")

db_live = "postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation"
with psycopg.connect(db_live) as conn_live:
    with conn_live.cursor() as cur_live:
        cur_live.execute("SELECT count(*) FROM annotations WHERE task_id = 48")
        count = cur_live.fetchone()[0]
        print(f"LIVE DB: {count} annotations")
