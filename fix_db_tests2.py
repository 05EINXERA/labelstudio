import re
import glob

# 1. Add fixture to conftest.py
with open("tests/conftest.py", "r", encoding="utf-8") as f:
    content = f.read()

if "def clear_db" not in content:
    content += """
from database import SessionLocal
from sqlalchemy import text

@pytest.fixture(autouse=True)
def clear_db():
    yield
    with SessionLocal() as db:
        db.execute(text("DELETE FROM annotations;"))
        db.execute(text("DELETE FROM tasks;"))
        db.execute(text("DELETE FROM labels;"))
        db.execute(text("DELETE FROM projects;"))
        db.commit()
"""
    with open("tests/conftest.py", "w", encoding="utf-8") as f:
        f.write(content)

# 2. Fix test files
for path in glob.glob("tests/*.py"):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    # Rule A: replace client.get(f"/api/tasks?projectId=...").json() with ["items"]
    # We only target routes with `projectId=` or similar query strings, NOT `/api/tasks/{tid}`
    src = re.sub(
        r'(client\.get\(f?"/api/tasks\?projectId=[^)]*\)\.json\(\))(?!\s*\["items"\])',
        r'\1["items"]',
        src
    )

    # Rule B: replace next(t for t in tasks if t["description"] == ... )["annotations"]
    # with getting the task ID and making a separate GET request.
    # We'll replace the block:
    # tasks = client.get(...).json()["items"]
    # anns = next(t for t in tasks if t["description"] == "rt.png")["annotations"]
    # OR return next(...)["annotations"]
    # This requires some clever regex or manual replaces.

    # Pattern for assigning to a variable (anns, imported, task, etc)
    src = re.sub(
        r'(\w+)\s*=\s*next\(t for t in tasks(?:\["items"\])? if t\["description"\] == ([^)]+)\)\["annotations"\]',
        r'_tid = next(t["id"] for t in tasks if t["description"] == \2)\n    \1 = client.get(f"/api/tasks/{_tid}", headers=alice).json()["annotations"]',
        src
    )
    # What if the header was auth or user or bob? Let's use `auth` for the general helper if we can, but tests use different ones.
    
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
