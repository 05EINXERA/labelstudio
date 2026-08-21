import os
import sys
sys.path.insert(0, os.path.abspath("."))
import psycopg
from psycopg import sql

# Connect to the default 'postgres' database to create a new one
conn_str = "postgresql://seinxera05:seinxera@127.0.0.1:5435/postgres"
try:
    with psycopg.connect(conn_str, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP DATABASE IF EXISTS annotation_restore")
            cur.execute("CREATE DATABASE annotation_restore")
    print("Database annotation_restore created successfully.")
except Exception as e:
    print(f"Error creating db: {e}")
