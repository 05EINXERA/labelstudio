import os
import sys
import subprocess

db_url = "postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation_restore"
pg_restore = r"C:\Program Files\PostgreSQL\18\bin\pg_restore.exe"
dump = r"C:\annot-backups\workspace-20260821-102528.dump"

env = os.environ.copy()
env["PGPASSWORD"] = "seinxera"

print(f"Testing restore on {dump}")
result = subprocess.run([
    pg_restore,
    "--clean",
    "--if-exists",
    "--no-owner",
    "--no-privileges",
    "-t", "annotations",
    "-d", db_url,
    dump
], check=False, env=env, capture_output=True, text=True)

print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)
