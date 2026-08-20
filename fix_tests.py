import os
import glob
import re

for test_file in glob.glob('tests/test_*.py'):
    with open(test_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    modified = False
    
    # Fix len(tasks) assertions
    if 'tasks = client.get(' in content and 'assert len(tasks) ==' in content:
        # Some tests might have `tasks = client.get(...).json()`
        # Change `len(tasks)` to `len(tasks["items"])` if not already done, OR better change the variable assignment
        new_content = re.sub(r'tasks = client\.get\(f"/api/tasks\?projectId=\{([^\}]+)\}(?:&include_annotations=true)?", headers=([^)]+)\)\.json\(\)',
                             r'tasks = client.get(f"/api/tasks?projectId={\1}", headers=\2).json()["items"]',
                             content)
        if new_content != content:
            content = new_content
            modified = True
            
    # Also handle the _task_annotations pattern
    new_content2 = re.sub(
        r'tasks = client\.get\(f"/api/tasks\?projectId=\{([^\}]+)\}(?:&include_annotations=true)?", headers=([^)]+)\)\.json\(\)\n\s+return next\(t for t in tasks(?:\["items"\])? if t\["description"\] == ([^)]+)\)\["annotations"\]',
        r'tasks = client.get(f"/api/tasks?projectId={\1}", headers=\2).json()["items"]\n    tid = next(t["id"] for t in tasks if t["description"] == \3)\n    return client.get(f"/api/tasks/{tid}", headers=\2).json()["annotations"]',
        content)
    if new_content2 != content:
        content = new_content2
        modified = True

    # Other patterns where `next(t for t in tasks if ...)` is used
    if modified:
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write(content)
