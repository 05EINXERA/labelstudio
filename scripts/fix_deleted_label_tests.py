import os
import glob

test_files = glob.glob('tests/test_*.py')

for f in test_files:
    with open(f, 'r', encoding='utf-8') as file:
        content = file.read()
    
    if '"labelId": "gone"' in content or '"labelId": "does-not-exist"' in content:
        # We need to replace these hardcoded non-existent labels with a deleted label flow
        print(f"Fixing {f}")
        # test_annotations_json_format.py
        if "test_annotation_for_deleted_label_is_skipped" in content:
            new_content = content.replace(
                'pid = _new_project(client, alice)\n    _new_task(client, alice, pid, "a.png", annotations=[\n        {"id": "a1", "labelId": "gone"',
                'pid = _new_project(client, alice)\n    lid = _new_label(client, alice, pid, "lbl-gone", "Gone")\n    _new_task(client, alice, pid, "a.png", annotations=[\n        {"id": "a1", "labelId": lid'
            )
            # if that didn't match, try to find the assignment of "gone"
            new_content = new_content.replace(
                '    _new_task(client, alice, pid, "a.png", annotations=[\n        {"id": "a1", "labelId": "gone"',
                '    lid = _new_label(client, alice, pid, "lbl-gone", "Gone")\n    _new_task(client, alice, pid, "a.png", annotations=[\n        {"id": "a1", "labelId": lid'
            )
            new_content = new_content.replace('client.get(f"/api/exports/{job_id}/download", headers=alice)', 'client.delete(f"/api/labels/{lid}", headers=alice)\n    return client.get(f"/api/exports/{job_id}/download", headers=alice)')
            
            # test_exports.py uses 'does-not-exist'
            new_content = new_content.replace(
                '_new_task(client, alice, pid, "a.png", annotations=[\n        {"id": "ann1", "labelId": "does-not-exist"',
                'lid = _new_label(client, alice, pid, "lbl-gone", "Gone")\n    _new_task(client, alice, pid, "a.png", annotations=[\n        {"id": "ann1", "labelId": lid'
            )

        with open(f, 'w', encoding='utf-8') as file:
            file.write(new_content)
