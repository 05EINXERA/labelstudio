"""Add project_reviewers: annotators the owner has appointed to review a project

Revision ID: c9e2f4a71b86
Revises: b8d4e1f70a23
Create Date: 2026-09-13 12:20:00.000000

A reviewer is an annotator (`team_members.name`), not a user account: the LAN
deployment shares one login, so a username identifies the account rather than a
person. This is the same identity that `tasks.assignee` and `projects.creator`
already carry, and the same one `X-Annotator-Name` selects.

The row is the appointment itself. `appointed_by` records which annotator made
it, so the Teams view can say who granted the role — useful when an owner
inherits a project and wants to know why someone holds it.

Many reviewers per project, so this is a join table rather than a column on
`projects`. The unique constraint makes re-appointing an existing reviewer a
no-op at the database level instead of quietly stacking duplicate rows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9e2f4a71b86'
down_revision: Union[str, Sequence[str], None] = 'b8d4e1f70a23'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the project_reviewers join table."""
    op.create_table(
        'project_reviewers',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('member_name', sa.String(), nullable=False),
        sa.Column('appointed_by', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        # The appointment follows the person: renaming or removing a team member
        # should not leave a reviewer row pointing at nobody.
        sa.ForeignKeyConstraint(['member_name'], ['team_members.name'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'member_name', name='uq_project_reviewer'),
    )
    # Every authorization check asks "is this annotator a reviewer of this
    # project", and the task list asks "which projects does this annotator
    # review" — both are served by these.
    op.create_index('ix_project_reviewers_project_id', 'project_reviewers', ['project_id'])
    op.create_index('ix_project_reviewers_member_name', 'project_reviewers', ['member_name'])


def downgrade() -> None:
    """Drop the project_reviewers join table."""
    op.drop_index('ix_project_reviewers_member_name', table_name='project_reviewers')
    op.drop_index('ix_project_reviewers_project_id', table_name='project_reviewers')
    op.drop_table('project_reviewers')
