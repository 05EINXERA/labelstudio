import os
os.environ['DATABASE_URL'] = 'postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation'

from sqlalchemy import create_engine, text, inspect
engine = create_engine(os.environ['DATABASE_URL'])
inspector = inspect(engine)

print("Annotations table columns:")
columns = inspector.get_columns('annotations')
for col in columns:
    print(f"  {col['name']}: {col['type']}")

print("\nAnnotations for task 243:")
with engine.connect() as conn:
    result = conn.execute(text('SELECT * FROM annotations WHERE task_id = 243 LIMIT 5'))
    rows = result.fetchall()
    if rows:
        print(f"Found {len(rows)} annotations")
        for row in rows:
            print(f"  {row}")
    else:
        print("No annotations found for task 243")
