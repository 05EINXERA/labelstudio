import re

# tests/test_interop_import_regression.py
with open("tests/test_interop_import_regression.py", "r", encoding="utf-8") as f:
    src = f.read()
src = src.replace(
    'total = sum(len(t["annotations"]) for t in tasks)',
    'total = sum(len(client.get(f"/api/tasks/{t[\'id\']}", headers=alice).json()["annotations"]) for t in tasks)'
)
src = src.replace(
    'stored = task["annotations"]',
    'stored = client.get(f"/api/tasks/{task[\'id\']}", headers=alice).json()["annotations"]'
)
with open("tests/test_interop_import_regression.py", "w", encoding="utf-8") as f:
    f.write(src)

# tests/test_task_save_conflicts.py
with open("tests/test_task_save_conflicts.py", "r", encoding="utf-8") as f:
    src = f.read()
src = src.replace(
    'assert len(rows[0]["annotations"]) == 4',
    'assert len(client.get(f"/api/tasks/{rows[0][\'id\']}", headers=alice).json()["annotations"]) == 4'
)
src = src.replace(
    'assert rows[0]["annotations"] == []',
    'assert client.get(f"/api/tasks/{rows[0][\'id\']}", headers=alice).json()["annotations"] == []'
)
src = src.replace(
    'assert "annotations" not in rows[0]  # performance win',
    'assert "annotations" not in rows[0]'
)
with open("tests/test_task_save_conflicts.py", "w", encoding="utf-8") as f:
    f.write(src)
