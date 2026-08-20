import re
import glob

replacements = [
    (
        r'(\w+)\s*=\s*next\(t for t in tasks(?:\["items"\])? if t\["description"\] == ([^)]+)\)\["annotations"\]',
        r'_tid = next(t["id"] for t in tasks if t["description"] == \2)\n    \1 = client.get(f"/api/tasks/{_tid}", headers=alice).json()["annotations"]'
    ),
    (
        r'return next\(t for t in tasks(?:\["items"\])? if t\["description"\] == ([^)]+)\)\["annotations"\]',
        r'_tid = next(t["id"] for t in tasks if t["description"] == \1)\n    return client.get(f"/api/tasks/{_tid}", headers=auth).json()["annotations"]'
    ),
    (
        r'task\s*=\s*next\(t for t in tasks(?:\["items"\])? if t\["description"\] == ([^)]+)\)\n\s+anns\s*=\s*task\["annotations"\]',
        r'_tid = next(t["id"] for t in tasks if t["description"] == \1)\n    anns = client.get(f"/api/tasks/{_tid}", headers=alice).json()["annotations"]'
    ),
    (
        r'tid\s*=\s*next\(t\["id"\] for t in tasks(?:\["items"\])? if t\["description"\] == ([^)]+)\)',
        r'tid = next(t["id"] for t in tasks if t["description"] == \1)'
    )
]

for path in glob.glob("tests/*.py"):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    modified = False
    for pattern, repl in replacements:
        new_src = re.sub(pattern, repl, src)
        if new_src != src:
            src = new_src
            modified = True
            
    # specifically fix test_import_export_formats.py which uses headers=user
    src = src.replace('headers=auth).json()["annotations"]', 'headers=user).json()["annotations"]')
    
    if modified:
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
