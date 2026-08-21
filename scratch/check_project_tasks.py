import os
import sys
sys.path.insert(0, os.path.abspath("."))
import psycopg

db_restore = "postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation_restore"

try:
    with psycopg.connect(db_restore) as conn_rest:
        with conn_rest.cursor() as cur_rest:
            # check if task 243 exists
            cur_rest.execute("SELECT id, project_id FROM tasks WHERE id = 48")
            task = cur_rest.fetchone()
            if not task:
                print("Task 48 does not exist in backup.")
            else:
                print(f"Task 48 exists in backup, project_id: {task[1]}")
                
            # check project name
            cur_rest.execute("SELECT id, name FROM projects WHERE name = 'AOT'")
            projects = cur_rest.fetchall()
            for p in projects:
                print(f"Project 'AOT' found with id: {p[0]}")
                # check tasks in this project
                cur_rest.execute("SELECT id FROM tasks WHERE project_id = %s", (p[0],))
                tasks = cur_rest.fetchall()
                print(f"  Contains {len(tasks)} tasks.")

except Exception as e:
    print(f"Error: {e}")
