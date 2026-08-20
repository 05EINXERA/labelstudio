import sqlite3
import psycopg
import json

# Connect to both databases
sqlite_conn = sqlite3.connect('workspace.db')
pg_conn = psycopg.connect('postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation')

# Fetch all tasks from SQLite
sqlite_tasks = sqlite_conn.execute("SELECT id, annotations FROM tasks").fetchall()
sqlite_tasks_dict = {t[0]: t[1] for t in sqlite_tasks if t[1] is not None and t[1] != '[]'}

# Fetch all annotations from Postgres
pg_anns = pg_conn.execute("SELECT task_id, count(id) FROM annotations GROUP BY task_id").fetchall()
pg_anns_dict = {t[0]: t[1] for t in pg_anns}

missing_tasks = []
discrepancies = []

for task_id, sqlite_ann_str in sqlite_tasks_dict.items():
    try:
        sqlite_anns = json.loads(sqlite_ann_str)
        if len(sqlite_anns) == 0:
            continue
            
        pg_count = pg_anns_dict.get(task_id, 0)
        if pg_count == 0:
            missing_tasks.append(task_id)
        elif len(sqlite_anns) != pg_count:
            # Note: there might be a legitimate difference if the user edited it in PG since migration
            discrepancies.append((task_id, len(sqlite_anns), pg_count))
    except Exception as e:
        print(f"Failed parsing task {task_id}: {e}")

print(f"Total SQLite tasks with annotations: {len(sqlite_tasks_dict)}")
print(f"Tasks missing from Postgres annotations: {len(missing_tasks)}")
print(f"Tasks with different counts: {len(discrepancies)}")
if missing_tasks:
    print(f"Missing task IDs: {missing_tasks[:10]}")
