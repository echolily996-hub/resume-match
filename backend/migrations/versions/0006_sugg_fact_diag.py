"""Add safe fact-support diagnostics for blocked suggestions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0006_sugg_fact_diag'
down_revision = '0005_sugg_block_diag'
branch_labels = None
depends_on = None
json_type = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade():
    op.add_column('suggestions', sa.Column(
        'fact_support_failures', json_type, nullable=False,
        server_default=sa.text("'[]'")))


def downgrade():
    op.drop_column('suggestions', 'fact_support_failures')
