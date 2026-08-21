import os
import sys
import subprocess

dumps = []
for root, _, files in os.walk(r"C:\annot-backups"):
    for f in files:
        if f.endswith(".dump"):
            dumps.append(os.path.join(root, f))
dumps.sort(reverse=True) # newest first

pg_restore = r"C:\Program Files\PostgreSQL\18\bin\pg_restore.exe"

found_dump = None
for dump in dumps:
    print(f"Checking {dump}...")
    try:
        result = subprocess.run([
            pg_restore,
            "-a",
            "-t", "annotations",
            dump
        ], capture_output=True, text=True)
        
        lines = result.stdout.split('\n')
        # We look for lines containing task_id 243
        # In a COPY statement, data is tab-separated, and the columns might be id, task_id, etc.
        # We can just look for the tab-separated "243" in the line.
        for line in lines:
            if '\t243\t' in line:
                print(f"  -> Found annotation for task 243 in {dump}")
                found_dump = dump
                break
        if found_dump:
            break
            
    except Exception as e:
        print(f"Error checking {dump}: {e}")

if found_dump:
    print(f"Found annotations in {found_dump}. Let's restore from this one.")
    
    # restore this to annotation_restore
    db_url = "postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation_restore"
    env = os.environ.copy()
    env["PGPASSWORD"] = "seinxera"
    try:
        print("Restoring...")
        subprocess.run([
            pg_restore,
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "-d", db_url,
            found_dump
        ], check=False, env=env)
        print("Done restoring.")
    except Exception as e:
        print(e)
else:
    print("Could not find any backup containing annotations for task 243.")
