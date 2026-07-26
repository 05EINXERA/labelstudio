"""Reset alembic_version to b2c3d4e5f6a7 so the two missing migrations
(workspace_data.owner_id and tasks.last_client_id) can be re-applied."""
import sqlite3
import sys
import os

db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "workspace.db")
if not os.path.exists(db_path):
    print(f"DB not found at {db_path}")
    sys.exit(1)

conn = sqlite3.connect(db_path)
# Reset to the revision BEFORE the two unapplied migrations
conn.execute("UPDATE alembic_version SET version_num = ?", ("b2c3d4e5f6a7",))
conn.commit()
row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
print(f"alembic_version is now: {row[0]}")
conn.close()
