"""Phase 2: immutable resume inputs and versioned jobs."""
from alembic import op
import sqlalchemy as sa

revision = '0002_resume_job'
down_revision = '0001_auth'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('resumes',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('original_text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('id', 'owner_id', name='uq_resumes_id_owner'))
    op.create_index('ix_resumes_owner_id', 'resumes', ['owner_id'])
    op.create_table('jobs',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('id', 'owner_id', name='uq_jobs_id_owner'))
    op.create_index('ix_jobs_owner_id', 'jobs', ['owner_id'])


def downgrade():
    op.drop_table('jobs')
    op.drop_table('resumes')
