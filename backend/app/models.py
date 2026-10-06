import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
                        Integer, String, Text, UniqueConstraint, Uuid)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

JSONValue = JSON().with_variant(JSONB(), 'postgresql')


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = 'users'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LoginSession(Base):
    __tablename__ = 'sessions'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Resume(Base):
    __tablename__ = 'resumes'
    __table_args__ = (UniqueConstraint('id', 'owner_id', name='uq_resumes_id_owner'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey('users.id', ondelete='CASCADE'), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Job(Base):
    __tablename__ = 'jobs'
    __table_args__ = (UniqueConstraint('id', 'owner_id', name='uq_jobs_id_owner'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey('users.id', ondelete='CASCADE'), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Analysis(Base):
    __tablename__ = 'analyses'
    __table_args__ = (
        ForeignKeyConstraint(['resume_id', 'owner_id'], ['resumes.id', 'resumes.owner_id'],
                             name='fk_analyses_resume_owner'),
        ForeignKeyConstraint(['job_id', 'owner_id'], ['jobs.id', 'jobs.owner_id'],
                             name='fk_analyses_job_owner'),
        UniqueConstraint('owner_id', 'request_key', name='uq_analyses_owner_request_key'),
        CheckConstraint("state IN ('queued','running','succeeded','failed')", name='ck_analyses_state'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey('users.id', ondelete='CASCADE'), index=True, nullable=False)
    resume_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    resume_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    job_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    job_description_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(20), default='queued', index=True, nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    request_key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    warnings: Mapped[list] = mapped_column(JSONValue, default=list, nullable=False)
    model_metadata: Mapped[dict | None] = mapped_column(JSONValue, nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(30), default='analysis-v1', nullable=False)
    validator_version: Mapped[str] = mapped_column(String(30), default='evidence-v1', nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnalysisRequirement(Base):
    __tablename__ = 'analysis_requirements'
    __table_args__ = (
        UniqueConstraint('analysis_id', 'ordinal', name='uq_requirements_analysis_ordinal'),
        UniqueConstraint('id', 'analysis_id', name='uq_requirements_id_analysis'),
        CheckConstraint(
            "match_status IN ('reflected','not_reflected','needs_confirmation')",
            name='ck_requirements_match_status'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey('analyses.id', ondelete='CASCADE'), index=True, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    requirement_key: Mapped[str] = mapped_column(String(80), nullable=False)
    job_quote: Mapped[str] = mapped_column(Text, nullable=False)
    job_span: Mapped[dict] = mapped_column(JSONValue, nullable=False)
    resume_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_span: Mapped[dict | None] = mapped_column(JSONValue, nullable=True)
    match_status: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    confirmation_questions: Mapped[list] = mapped_column(JSONValue, default=list, nullable=False)


class Suggestion(Base):
    __tablename__ = 'suggestions'
    __table_args__ = (
        UniqueConstraint('analysis_id', 'source_segment_id',
                         name='uq_suggestions_analysis_segment'),
        ForeignKeyConstraint(['requirement_id', 'analysis_id'],
                             ['analysis_requirements.id', 'analysis_requirements.analysis_id'],
                             name='fk_suggestions_requirement_analysis', ondelete='CASCADE'),
        CheckConstraint("validation_status IN ('actionable','blocked')",
                        name='ck_suggestions_validation_status'),
        CheckConstraint("decision IN ('pending','accepted','ignored')",
                        name='ck_suggestions_decision'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey('analyses.id', ondelete='CASCADE'), index=True, nullable=False)
    requirement_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True, nullable=False)
    source_segment_id: Mapped[str] = mapped_column(String(40), nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    rewrite_reason: Mapped[str] = mapped_column(Text, nullable=False)
    template_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    template_args: Mapped[dict] = mapped_column(JSONValue, default=dict, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(20), nullable=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_detail_codes: Mapped[list] = mapped_column(JSONValue, default=list, nullable=False)
    fact_support_failures: Mapped[list] = mapped_column(JSONValue, default=list, nullable=False)
    decision: Mapped[str] = mapped_column(String(20), default='pending', nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Draft(Base):
    __tablename__ = 'drafts'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey('analyses.id', ondelete='CASCADE'), unique=True, index=True, nullable=False)
    working_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    edit_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    saved_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class SavedRevision(Base):
    __tablename__ = 'saved_revisions'
    __table_args__ = (
        UniqueConstraint('draft_id', 'version', name='uq_saved_revisions_draft_version'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey('drafts.id', ondelete='CASCADE'), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion_decisions: Mapped[dict] = mapped_column(JSONValue, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False)
