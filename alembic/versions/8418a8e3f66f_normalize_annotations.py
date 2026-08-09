"""normalize_annotations

Revision ID: 8418a8e3f66f
Revises: e5f6a7b8c9d0
Create Date: 2026-08-09 15:45:51.956782

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8418a8e3f66f'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


import json
import uuid

def upgrade() -> None:
    """Upgrade schema."""
    # 1. Create table
    op.create_table(
        'annotations',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('label_id', sa.String(), nullable=True),
        sa.Column('type', sa.String(length=32), nullable=False),
        sa.Column('points', sa.Text(), nullable=True),
        sa.Column('x', sa.Float(), nullable=True),
        sa.Column('y', sa.Float(), nullable=True),
        sa.Column('width', sa.Float(), nullable=True),
        sa.Column('height', sa.Float(), nullable=True),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('color', sa.String(length=16), nullable=True),
        sa.Column('order', sa.Integer(), nullable=True),
        sa.Column('group_id', sa.String(length=36), nullable=True),
        sa.Column('extra', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.ForeignKeyConstraint(['label_id'], ['labels.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', 'task_id')
    )
    op.create_index(op.f('ix_annotations_group_id'), 'annotations', ['group_id'], unique=False)
    op.create_index(op.f('ix_annotations_label_id'), 'annotations', ['label_id'], unique=False)
    op.create_index(op.f('ix_annotations_task_id'), 'annotations', ['task_id'], unique=False)

    # 2. Migrate data
    bind = op.get_bind()
    tasks = bind.execute(sa.text("SELECT id, annotations FROM tasks WHERE annotations IS NOT NULL AND annotations != ''")).fetchall()
    
    annotations_data = []
    for task_id, anns_str in tasks:
        try:
            anns = json.loads(anns_str)
            for a in anns:
                if not isinstance(a, dict):
                    continue
                # Extract known fields
                aid = a.get('id') or str(uuid.uuid4())
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
                
                # everything else goes to extra
                known_keys = {'id', 'type', 'labelId', 'points', 'x', 'y', 'width', 'height', 'text', 'color', 'order', 'groupId'}
                extra_dict = {k: v for k, v in a.items() if k not in known_keys}
                extra = json.dumps(extra_dict) if extra_dict else None
                
                annotations_data.append({
                    'id': str(aid),
                    'task_id': task_id,
                    'label_id': str(label_id) if label_id is not None else None,
                    'type': str(atype),
                    'points': points,
                    'x': float(x) if x is not None else None,
                    'y': float(y) if y is not None else None,
                    'width': float(w) if w is not None else None,
                    'height': float(h) if h is not None else None,
                    'text': str(text) if text is not None else None,
                    'color': str(color) if color is not None else None,
                    'order': int(order) if order is not None else None,
                    'group_id': str(group_id) if group_id is not None else None,
                    'extra': extra
                })
        except Exception:
            pass
            
    if annotations_data:
        chunk_size = 100
        meta = sa.MetaData()
        annotations_table = sa.Table('annotations', meta, 
                                     sa.Column('id', sa.String(length=64)),
                                     sa.Column('task_id', sa.Integer()),
                                     sa.Column('label_id', sa.String()),
                                     sa.Column('type', sa.String(length=32)),
                                     sa.Column('points', sa.Text()),
                                     sa.Column('x', sa.Float()),
                                     sa.Column('y', sa.Float()),
                                     sa.Column('width', sa.Float()),
                                     sa.Column('height', sa.Float()),
                                     sa.Column('text', sa.Text()),
                                     sa.Column('color', sa.String(length=16)),
                                     sa.Column('order', sa.Integer()),
                                     sa.Column('group_id', sa.String(length=36)),
                                     sa.Column('extra', sa.Text()))
        for i in range(0, len(annotations_data), chunk_size):
            chunk = annotations_data[i:i + chunk_size]
            bind.execute(annotations_table.insert(), chunk)

    # 3. Rename annotations -> annotations_legacy
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.alter_column('annotations', new_column_name='annotations_legacy')

def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.alter_column('annotations_legacy', new_column_name='annotations')
        
    op.drop_index(op.f('ix_annotations_task_id'), table_name='annotations')
    op.drop_index(op.f('ix_annotations_label_id'), table_name='annotations')
    op.drop_index(op.f('ix_annotations_group_id'), table_name='annotations')
    op.drop_table('annotations')
