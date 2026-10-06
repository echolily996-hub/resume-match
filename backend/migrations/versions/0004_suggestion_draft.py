"""Phase 5: suggestions, decisions and independent working drafts."""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0004_suggestion_draft'
down_revision = '0003_analysis'
branch_labels = None
depends_on = None
json_type = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def initial_markdown(original_text):
    recognized = {'教育背景', '技能', '项目经历', '工作经历'}
    lines = original_text.splitlines(keepends=True)
    rendered = []
    found_heading = False
    for line in lines:
        body = line.rstrip('\r\n')
        ending = line[len(body):]
        if body.strip().rstrip('：:') in recognized:
            rendered.append(f"## {body.strip().rstrip('：:')}{ending}")
            found_heading = True
        else:
            rendered.append(line)
    return ''.join(rendered) if found_heading else f'# 简历正文\n\n{original_text}'


def upgrade():
    with op.batch_alter_table('analysis_requirements') as batch:
        batch.create_unique_constraint(
            'uq_requirements_id_analysis', ['id', 'analysis_id'])
    op.create_table('suggestions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('analysis_id', sa.Uuid(), sa.ForeignKey('analyses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('requirement_id', sa.Uuid(), nullable=False),
        sa.Column('source_segment_id', sa.String(40), nullable=False),
        sa.Column('original_text', sa.Text(), nullable=False),
        sa.Column('suggested_text', sa.Text(), nullable=True),
        sa.Column('rewrite_reason', sa.Text(), nullable=False),
        sa.Column('template_id', sa.String(80), nullable=True),
        sa.Column('template_args', json_type, nullable=False),
        sa.Column('validation_status', sa.String(20), nullable=False),
        sa.Column('blocked_reason', sa.Text(), nullable=True),
        sa.Column('decision', sa.String(20), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("validation_status IN ('actionable','blocked')",
                           name='ck_suggestions_validation_status'),
        sa.CheckConstraint("decision IN ('pending','accepted','ignored')",
                           name='ck_suggestions_decision'),
        sa.ForeignKeyConstraint(['requirement_id', 'analysis_id'],
            ['analysis_requirements.id', 'analysis_requirements.analysis_id'],
            name='fk_suggestions_requirement_analysis', ondelete='CASCADE'),
        sa.UniqueConstraint('analysis_id', 'source_segment_id',
                            name='uq_suggestions_analysis_segment'))
    op.create_index('ix_suggestions_analysis_id', 'suggestions', ['analysis_id'])
    op.create_index('ix_suggestions_requirement_id', 'suggestions', ['requirement_id'])
    op.create_table('drafts',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('analysis_id', sa.Uuid(), sa.ForeignKey('analyses.id', ondelete='CASCADE'),
                  nullable=False, unique=True),
        sa.Column('working_markdown', sa.Text(), nullable=False),
        sa.Column('edit_version', sa.Integer(), nullable=False),
        sa.Column('saved_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_drafts_analysis_id', 'drafts', ['analysis_id'], unique=True)

    analyses = sa.table('analyses',
        sa.column('id', sa.Uuid()), sa.column('resume_snapshot', sa.Text()),
        sa.column('created_at', sa.DateTime(timezone=True)))
    drafts = sa.table('drafts',
        sa.column('id', sa.Uuid()), sa.column('analysis_id', sa.Uuid()),
        sa.column('working_markdown', sa.Text()), sa.column('edit_version', sa.Integer()),
        sa.column('saved_version', sa.Integer()),
        sa.column('created_at', sa.DateTime(timezone=True)),
        sa.column('updated_at', sa.DateTime(timezone=True)))
    rows = op.get_bind().execute(sa.select(
        analyses.c.id, analyses.c.resume_snapshot, analyses.c.created_at)).mappings().all()
    if rows:
        op.bulk_insert(drafts, [{
            'id': uuid.uuid4(), 'analysis_id': row['id'],
            'working_markdown': initial_markdown(row['resume_snapshot']), 'edit_version': 1,
            'saved_version': 0, 'created_at': row['created_at'],
            'updated_at': row['created_at'],
        } for row in rows])


def downgrade():
    op.drop_table('drafts')
    op.drop_table('suggestions')
    with op.batch_alter_table('analysis_requirements') as batch:
        batch.drop_constraint('uq_requirements_id_analysis', type_='unique')
