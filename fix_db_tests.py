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

# 2. Fix pagination .json() -> .json()["items"]
for path in glob.glob("tests/*.py"):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    
    src = re.sub(
        r'(client\.get\(f?"/api/tasks[^)]*\)\.json\(\))(?!\s*\["items"\])',
        r'\1["items"]',
        src
    )
    
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
