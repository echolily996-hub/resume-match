"""Add safe diagnostic codes for blocked suggestions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005_sugg_block_diag'
down_revision = '0004_suggestion_draft'
branch_labels = None
depends_on = None
json_type = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade():
    op.add_column('suggestions', sa.Column(
        'blocked_detail_codes', json_type, nullable=False,
        server_default=sa.text("'[]'")))


def downgrade():
    op.drop_column('suggestions', 'blocked_detail_codes')

