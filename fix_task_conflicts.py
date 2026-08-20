import re

with open("tests/test_task_save_conflicts.py", "r", encoding="utf-8") as f:
    src = f.read()

src = src.replace(
    ').json()',
    ').json()["items"]'
)

with open("tests/test_task_save_conflicts.py", "w", encoding="utf-8") as f:
    f.write(src)
