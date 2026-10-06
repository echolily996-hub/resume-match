"""Phase 3: analysis snapshots, state, metadata and structured requirements."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0003_analysis'
down_revision = '0002_resume_job'
branch_labels = None
depends_on = None
json_type = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade():
    op.create_table('analyses',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('resume_id', sa.Uuid(), nullable=False),
        sa.Column('job_id', sa.Uuid(), nullable=False),
        sa.Column('resume_snapshot', sa.Text(), nullable=False),
        sa.Column('job_name_snapshot', sa.String(200), nullable=False),
        sa.Column('job_description_snapshot', sa.Text(), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('attempt_no', sa.Integer(), nullable=False),
        sa.Column('request_key', sa.String(100), nullable=False),
        sa.Column('request_fingerprint', sa.String(64), nullable=False),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_code', sa.String(80), nullable=True),
        sa.Column('error_message', sa.String(500), nullable=True),
        sa.Column('warnings', json_type, nullable=False),
        sa.Column('model_metadata', json_type, nullable=True),
        sa.Column('prompt_version', sa.String(30), nullable=False),
        sa.Column('validator_version', sa.String(30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('queued','running','succeeded','failed')", name='ck_analyses_state'),
        sa.ForeignKeyConstraint(['resume_id', 'owner_id'], ['resumes.id', 'resumes.owner_id'],
                                name='fk_analyses_resume_owner'),
        sa.ForeignKeyConstraint(['job_id', 'owner_id'], ['jobs.id', 'jobs.owner_id'],
                                name='fk_analyses_job_owner'),
        sa.UniqueConstraint('owner_id', 'request_key', name='uq_analyses_owner_request_key'))
    op.create_index('ix_analyses_owner_id', 'analyses', ['owner_id'])
    op.create_index('ix_analyses_state', 'analyses', ['state'])
    op.create_table('analysis_requirements',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('analysis_id', sa.Uuid(), sa.ForeignKey('analyses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('requirement_key', sa.String(80), nullable=False),
        sa.Column('job_quote', sa.Text(), nullable=False),
        sa.Column('job_span', json_type, nullable=False),
        sa.Column('resume_quote', sa.Text(), nullable=True),
        sa.Column('resume_span', json_type, nullable=True),
        sa.Column('match_status', sa.String(30), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('confirmation_questions', json_type, nullable=False),
        sa.CheckConstraint("match_status IN ('reflected','not_reflected','needs_confirmation')",
                           name='ck_requirements_match_status'),
        sa.UniqueConstraint('analysis_id', 'ordinal', name='uq_requirements_analysis_ordinal'))
    op.create_index('ix_analysis_requirements_analysis_id', 'analysis_requirements', ['analysis_id'])


def downgrade():
    op.drop_table('analysis_requirements')
    op.drop_table('analyses')
