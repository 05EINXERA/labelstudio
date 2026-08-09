import os

def swap_lines(filepath, search1, search2):
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    for i in range(len(lines) - 1):
        if search1 in lines[i] and search2 in lines[i+1]:
            # swap
            lines[i], lines[i+1] = lines[i+1], lines[i]
            break
            
    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(lines)

swap_lines("tests/test_yolo_format.py", "entries, _ = _export_yolo", "client.delete")
swap_lines("tests/test_masks_format.py", "entries, _ = _export_masks", "client.delete")
