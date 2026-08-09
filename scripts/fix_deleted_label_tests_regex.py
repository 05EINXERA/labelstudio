import os
import glob
import re

test_files = glob.glob('tests/test_*.py')

for f in test_files:
    with open(f, 'r', encoding='utf-8') as file:
        content = file.read()
    
    if "test_annotation_for_deleted_label_is_skipped" in content:
        # We need to rewrite the function
        # It usually looks like:
        # def test_annotation_for_deleted_label_is_skipped(client, alice):
        #     pid = _new_project(client, alice)
        #     _new_task(client, alice, pid, "a.png", annotations=[
        #         {"id": "...", "labelId": "...", ...}
        #     ])
        #     assert ...
        
        # We want to insert:
        #     lid = _new_label(client, alice, pid, "lbl-gone", "Gone")
        # And replace "labelId": "gone" (or does-not-exist) with lid
        # And then after _new_task, add client.delete(f"/api/labels/{lid}", headers=alice)
        
        print(f"Modifying {f}")
        
        # Find the function block
        func_match = re.search(r'(def test_annotation_for_deleted_label_is_skipped\(.*?\):.*?)(?=\n\n\n|\Z)', content, flags=re.DOTALL)
        if not func_match:
            print("Could not match function body")
            continue
            
        func_body = func_match.group(1)
        
        if "client.delete" in func_body:
            print("Already fixed")
            continue
            
        # Insert lid creation after pid = ...
        new_func_body = re.sub(
            r'(pid = _new_project\(.*?\)\n)',
            r'\1    lid = _new_label(client, alice, pid, "lbl-gone", "Gone")\n',
            func_body
        )
        
        # Replace "labelId": "gone" or "does-not-exist" with "labelId": lid
        new_func_body = re.sub(r'"labelId": "(?:gone|does-not-exist)"', r'"labelId": lid', new_func_body)
        
        # Insert client.delete after _new_task(...)
        # This is tricky because _new_task spans multiple lines. We can just insert it before the assert.
        new_func_body = re.sub(
            r'(\n    assert )',
            r'\n    client.delete(f"/api/labels/{lid}", headers=alice)\1',
            new_func_body
        )
        
        content = content.replace(func_body, new_func_body)
        
        with open(f, 'w', encoding='utf-8') as file:
            file.write(content)
