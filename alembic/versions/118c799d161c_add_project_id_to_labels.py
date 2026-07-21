"""Add project_id to labels

Revision ID: 118c799d161c
Revises: 97472310f3a2
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '118c799d161c'
down_revision: Union[str, Sequence[str], None] = '97472310f3a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('labels') as batch_op:
        batch_op.add_column(sa.Column('project_id', sa.Integer(), nullable=True))
        batch_op.create_index(op.f('ix_labels_project_id'), ['project_id'], unique=False)
        batch_op.create_foreign_key('fk_labels_project_id_projects', 'projects', ['project_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('labels') as batch_op:
        batch_op.drop_constraint('fk_labels_project_id_projects', type_='foreignkey')
        batch_op.drop_index(op.f('ix_labels_project_id'))
        batch_op.drop_column('project_id')
