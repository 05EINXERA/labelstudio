import os
import glob
import re

test_files = glob.glob('tests/test_*.py')

for f in test_files:
    with open(f, 'r', encoding='utf-8') as file:
        content = file.read()
    
    # We want to replace client.get(f"/api/tasks?projectId={pid}", headers=alice)
    # with client.get(f"/api/tasks?projectId={pid}&include_annotations=true", headers=alice)
    # This might use {target}, {src}, {pid}, etc.
    
    new_content = re.sub(
        r'client\.get\(f"/api/tasks\?projectId=\{([a-zA-Z0-9_]+)\}"',
        r'client.get(f"/api/tasks?projectId={\1}&include_annotations=true"',
        content
    )
    
    if new_content != content:
        print(f"Fixing {f}")
        with open(f, 'w', encoding='utf-8') as file:
            file.write(new_content)
