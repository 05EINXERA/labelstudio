import os
import sqlite3
import sys

# Add the parent directory to sys.path so we can import from database and models
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from config import DATA_DIR
import models

# 1. Connect to SQLite
sqlite_path = os.path.join(DATA_DIR, "workspace.db")
if not os.path.exists(sqlite_path):
    print(f"SQLite database not found at {sqlite_path}")
    sys.exit(1)

sqlite_conn = sqlite3.connect(sqlite_path)
sqlite_conn.row_factory = sqlite3.Row
sqlite_cursor = sqlite_conn.cursor()

# 2. Connect to PostgreSQL
pg_url = os.getenv("DATABASE_URL")
if not pg_url or "postgres" not in pg_url:
    print("Please set DATABASE_URL environment variable to your PostgreSQL connection string.")
    print("Example: set DATABASE_URL=postgresql+pg8000://postgres:mypassword@localhost:5432/labelstudio")
    sys.exit(1)

pg_engine = create_engine(pg_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)
pg_session = SessionLocal()

# Verify connection and schema exist
try:
    pg_session.execute(text("SELECT 1")).scalar()
except Exception as e:
    print(f"Failed to connect to PostgreSQL: {e}")
    sys.exit(1)

# List of tables to migrate in order of dependencies
tables = [
    "users",
    "projects",
    "tasks",
    "team_members",
    "labels",
    "workspace_data"
]

print("Starting migration...")

for table in tables:
    print(f"Migrating table: {table}...")
    sqlite_cursor.execute(f"SELECT * FROM {table}")
    rows = sqlite_cursor.fetchall()
    
    if not rows:
        print(f"  No rows found in {table}, skipping.")
        continue
    
    # Get column names
    columns = rows[0].keys()
    
    # Build the insert query
    cols_str = ", ".join(columns)
    placeholders = ", ".join([f":{col}" for col in columns])
    insert_query = text(f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders})")
    
    count = 0
    for row in rows:
        # Convert row to dict
        data = dict(row)
        try:
            pg_session.execute(insert_query, data)
            count += 1
        except Exception as e:
            print(f"  Error inserting row into {table}: {e}")
            pg_session.rollback()
            raise e
            
    print(f"  Successfully inserted {count} rows into {table}.")

# Update sequences for auto-incrementing primary keys
print("Updating sequences...")
sequences = {
    "users": "id",
    "projects": "id",
    "tasks": "id"
}

for table, pk_col in sequences.items():
    try:
        # Get the max id
        max_id = pg_session.execute(text(f"SELECT MAX({pk_col}) FROM {table}")).scalar()
        if max_id is not None:
            # Set the sequence to the max id
            seq_name = f"{table}_{pk_col}_seq"
            pg_session.execute(text(f"SELECT setval('{seq_name}', {max_id})"))
            print(f"  Updated sequence for {table} to {max_id}.")
    except Exception as e:
        print(f"  Could not update sequence for {table}: {e}")

pg_session.commit()
print("Migration completed successfully!")
