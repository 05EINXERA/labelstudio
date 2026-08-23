import os
os.environ['DATABASE_URL'] = 'postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation'

from sqlalchemy import create_engine, text

engine = create_engine(os.environ['DATABASE_URL'])

print("Checking task 243...")
with engine.connect() as conn:
    result = conn.execute(text('SELECT id, project_id, description, status FROM tasks WHERE id = 243'))
    task = result.fetchone()
    if task:
        print(f"✓ Task found:")
        print(f"  ID: {task[0]}")
        print(f"  Project ID: {task[1]}")
        print(f"  Description: {task[2]}")
        print(f"  Status: {task[3]}")
    else:
        print("✗ Task 243 not found")

print("\nChecking for annotations in task 243...")
with engine.connect() as conn:
    result = conn.execute(text('SELECT COUNT(*) FROM annotations WHERE task_id = 243'))
    count = result.scalar()
    print(f"Annotations in task 243: {count}")

print("\nChecking for tasks in project 42...")
with engine.connect() as conn:
    result = conn.execute(text('SELECT id, description FROM tasks WHERE project_id = 42 LIMIT 10'))
    tasks = result.fetchall()
    print(f"Found {len(tasks)} tasks in project 42:")
    for task in tasks:
        print(f"  Task {task[0]}: {task[1]}")
