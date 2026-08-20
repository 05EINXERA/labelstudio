import psycopg
import json

pg_conn = psycopg.connect('postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation')

pg_legacy_tasks = pg_conn.execute("SELECT id, annotations_legacy FROM tasks WHERE annotations_legacy IS NOT NULL AND annotations_legacy != '[]'").fetchall()
pg_legacy_dict = {t[0]: t[1] for t in pg_legacy_tasks}

pg_anns = pg_conn.execute("SELECT task_id, count(id) FROM annotations GROUP BY task_id").fetchall()
pg_anns_dict = {t[0]: t[1] for t in pg_anns}

print("Task ID | Legacy Count | New Postgres Count | Difference")
print("-" * 60)
for task_id, legacy_str in pg_legacy_dict.items():
    try:
        legacy_anns = json.loads(legacy_str)
        if len(legacy_anns) == 0:
            continue
            
        pg_count = pg_anns_dict.get(task_id, 0)
        if len(legacy_anns) != pg_count:
            diff = pg_count - len(legacy_anns)
            print(f"Task {task_id:<4} | {len(legacy_anns):<12} | {pg_count:<18} | {diff:+}")
    except Exception as e:
        pass
