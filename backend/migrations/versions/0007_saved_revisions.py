"""Add immutable saved draft revisions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0007_saved_revisions'
down_revision = '0006_sugg_fact_diag'
branch_labels = None
depends_on = None
json_type = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade():
    op.create_table(
        'saved_revisions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column(
            'draft_id', sa.Uuid(),
            sa.ForeignKey('drafts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('body_markdown', sa.Text(), nullable=False),
        sa.Column('suggestion_decisions', json_type, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            'draft_id', 'version', name='uq_saved_revisions_draft_version'),
    )
    op.create_index(
        'ix_saved_revisions_draft_id', 'saved_revisions', ['draft_id'])


def downgrade():
    op.drop_index('ix_saved_revisions_draft_id', table_name='saved_revisions')
    op.drop_table('saved_revisions')
