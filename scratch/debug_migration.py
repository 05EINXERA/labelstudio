import psycopg
import json
import uuid

conn = psycopg.connect('postgresql://seinxera05:seinxera@127.0.0.1:5435/annotation')
tasks = conn.execute("SELECT id, annotations_legacy FROM tasks WHERE annotations_legacy IS NOT NULL AND annotations_legacy != '[]' AND id NOT IN (SELECT task_id FROM annotations)").fetchall()
print(f'Found {len(tasks)} tasks')

annotations_data = []
for task_id, anns_str in tasks:
    try:
        anns = json.loads(anns_str)
        for a in anns:
            if not isinstance(a, dict):
                continue
            # Generate a fresh UUID to avoid any PK conflicts
            aid = str(uuid.uuid4())
            atype = a.get('type', 'polygon')
            label_id = a.get('labelId')
            points = a.get('points')
            if points is not None:
                points = json.dumps(points)
            x = a.get('x')
            y = a.get('y')
            w = a.get('width')
            h = a.get('height')
            text = a.get('text')
            color = a.get('color')
            order = a.get('order')
            group_id = a.get('groupId')
            
            known_keys = {'id', 'type', 'labelId', 'points', 'x', 'y', 'width', 'height', 'text', 'color', 'order', 'groupId'}
            extra_dict = {k: v for k, v in a.items() if k not in known_keys}
            extra = json.dumps(extra_dict) if extra_dict else None
            
            annotations_data.append((
                aid,
                task_id,
                str(label_id) if label_id is not None else None,
                str(atype),
                points,
                float(x) if x is not None else None,
                float(y) if y is not None else None,
                float(w) if w is not None else None,
                float(h) if h is not None else None,
                str(text) if text is not None else None,
                str(color) if color is not None else None,
                int(order) if order is not None else None,
                str(group_id) if group_id is not None else None,
                extra
            ))
    except Exception as e:
        print(f"Task {task_id} failed: {e}")

if annotations_data:
    try:
        with conn.transaction():
            for row in annotations_data:
                conn.execute('''
                    INSERT INTO annotations (id, task_id, label_id, type, points, x, y, width, height, text, color, "order", group_id, extra)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', row)
        conn.commit()
        print(f"Migrated {len(annotations_data)} annotations successfully!")
    except Exception as e:
        print(f"Insert failed: {e}")
