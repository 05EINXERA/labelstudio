import os
import glob
import re

test_files = glob.glob('tests/test_*.py')

for f in test_files:
    with open(f, 'r', encoding='utf-8') as file:
        content = file.read()
    
    if "client.delete(f\"/api/labels/{lid}\", headers=alice)" in content:
        print(f"Fixing {f}")
        content = content.replace(
            'client.delete(f"/api/labels/{lid}", headers=alice)',
            'client.delete(f"/api/labels/{lid}?projectId={pid}", headers=alice)'
        )
        with open(f, 'w', encoding='utf-8') as file:
            file.write(content)
