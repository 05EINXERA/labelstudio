import os
import sys
import shutil
import subprocess

def find_pg_restore():
    found = shutil.which("pg_restore")
    if found:
        return found
    candidates = [
        r"C:\Program Files\PostgreSQL",
        r"C:\Program Files (x86)\PostgreSQL",
    ]
    for base in candidates:
        if os.path.isdir(base):
            try:
                entries = sorted(os.listdir(base), reverse=True)
            except Exception:
                continue
            for entry in entries:
                path = os.path.join(base, entry, "bin", "pg_restore.exe")
                if os.path.isfile(path):
                    return path
    raise FileNotFoundError("pg_restore not found")

dump_file = r"C:\annot-backups\workspace-20260821-102528.dump"
db_url = "postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation_restore"

pg_restore = find_pg_restore()
print(f"Using pg_restore: {pg_restore}")

env = os.environ.copy()
env["PGPASSWORD"] = "seinxera"
try:
    subprocess.run([
        pg_restore,
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "-d", db_url,
        dump_file
    ], check=True, env=env)
    print("Restore completed successfully.")
except subprocess.CalledProcessError as e:
    print(f"Restore failed: {e}")
