"""Inspect Postgres schema to see what columns exist and whether alembic_version is present."""
import psycopg2
import urllib.parse
import os
from dotenv import load_dotenv

load_dotenv()
url = os.getenv("DATABASE_URL")
u = urllib.parse.urlparse(url.replace("postgresql+psycopg2", "postgresql"))
conn = psycopg2.connect(
    host=u.hostname, port=u.port,
    dbname=u.path.lstrip("/"), user=u.username, password=u.password
)
cur = conn.cursor()

cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='tasks' ORDER BY ordinal_position")
print("tasks columns:", [r[0] for r in cur.fetchall()])

cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='workspace_data' ORDER BY ordinal_position")
print("workspace_data columns:", [r[0] for r in cur.fetchall()])

cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='alembic_version')")
print("alembic_version exists:", cur.fetchone()[0])

conn.close()
