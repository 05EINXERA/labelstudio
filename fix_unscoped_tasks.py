with open("tests/test_import_export_formats.py", "r", encoding="utf-8") as f:
    src = f.read()

src = src.replace(
    'tasks = client.get("/api/tasks", params={"projectId": pid, "include_annotations": "true"}, headers=auth).json()',
    'tasks = client.get("/api/tasks", params={"projectId": pid}, headers=auth).json()["items"]'
)

with open("tests/test_import_export_formats.py", "w", encoding="utf-8") as f:
    f.write(src)

with open("tests/test_project_ownership.py", "r", encoding="utf-8") as f:
    src = f.read()

src = src.replace(
    'bob_tasks = client.get("/api/tasks", headers=bob).json()',
    'bob_tasks = client.get("/api/tasks", headers=bob).json()["items"]'
)
src = src.replace(
    'alice_tasks = client.get("/api/tasks", headers=alice).json()',
    'alice_tasks = client.get("/api/tasks", headers=alice).json()["items"]'
)

with open("tests/test_project_ownership.py", "w", encoding="utf-8") as f:
    f.write(src)
