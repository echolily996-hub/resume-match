import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .auth import AuthContext, current_auth, require_csrf
from .db import get_db
from .models import (Analysis, AnalysisRequirement, Draft, Job, Resume, SavedRevision,
                     Suggestion, utcnow)
from .resources import not_found

router = APIRouter(prefix='/api/v1/analyses')


class AnalysisCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resume_id: uuid.UUID
    job_id: uuid.UUID


class AnalysisRetry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_attempt_no: int = Field(ge=1)


class SuggestionDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision: Literal['accepted', 'ignored']
    working_markdown: str | None = Field(default=None, max_length=50_000)
    expected_edit_version: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def accepted_requires_draft(self):
        if self.decision == 'accepted':
            if self.working_markdown is None or self.expected_edit_version is None:
                raise ValueError('接受建议时必须提交当前修改稿和版本')
        elif self.working_markdown is not None or self.expected_edit_version is not None:
            raise ValueError('忽略建议不接受修改稿字段')
        return self


class DraftSave(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body_markdown: str = Field(max_length=50_000)
    expected_edit_version: int = Field(ge=1)


class DraftRestore(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_edit_version: int = Field(ge=1)


def fingerprint(resume: Resume, job: Job):
    encoded = json.dumps({
        'resume_id': str(resume.id), 'resume_text': resume.original_text,
        'job_id': str(job.id), 'job_name': job.name, 'job_description': job.description,
    }, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def initial_markdown(original_text: str):
    recognized = {'教育背景', '技能', '项目经历', '工作经历'}
    lines = original_text.splitlines(keepends=True)
    found_heading = False
    rendered = []
    for line in lines:
        body = line.rstrip('\r\n')
        ending = line[len(body):]
        if body.strip().rstrip('：:') in recognized:
            rendered.append(f"## {body.strip().rstrip('：:')}{ending}")
            found_heading = True
        else:
            rendered.append(line)
    return ''.join(rendered) if found_heading else f'# 简历正文\n\n{original_text}'


def summary(item: Analysis):
    return {
        'id': str(item.id), 'resume_id': str(item.resume_id), 'job_id': str(item.job_id),
        'job_name_snapshot': item.job_name_snapshot, 'state': item.state,
        'attempt_no': item.attempt_no, 'error_code': item.error_code,
        'error_message': item.error_message, 'created_at': item.created_at,
        'started_at': item.started_at, 'finished_at': item.finished_at,
    }


def suggestion_json(item: Suggestion):
    return {
        'id': str(item.id), 'requirement_id': str(item.requirement_id),
        'source_segment_id': item.source_segment_id,
        'original_text': item.original_text, 'suggested_text': item.suggested_text,
        'rewrite_reason': item.rewrite_reason,
        'validation_status': item.validation_status,
        'blocked_reason': item.blocked_reason,
        'blocked_detail_codes': item.blocked_detail_codes,
        'fact_support_failures': item.fact_support_failures,
        'decision': item.decision,
        'decided_at': item.decided_at,
    }


def draft_json(item: Draft, saved_revision: SavedRevision | None = None):
    return {
        'id': str(item.id), 'analysis_id': str(item.analysis_id),
        'working_markdown': item.working_markdown,
        'edit_version': item.edit_version, 'saved_version': item.saved_version,
        'saved_markdown': saved_revision.body_markdown if saved_revision else None,
        'created_at': item.created_at, 'updated_at': item.updated_at,
    }


def revision_summary(item: SavedRevision):
    return {
        'id': str(item.id), 'draft_id': str(item.draft_id),
        'version': item.version, 'created_at': item.created_at,
    }


def revision_json(item: SavedRevision):
    return {
        **revision_summary(item),
        'body_markdown': item.body_markdown,
        'suggestion_decisions': item.suggestion_decisions,
    }


def current_saved_revision(db: Session, draft: Draft):
    if draft.saved_version == 0:
        return None
    return db.scalar(select(SavedRevision).where(
        SavedRevision.draft_id == draft.id,
        SavedRevision.version == draft.saved_version))


def suggestion_decision_snapshot(db: Session, analysis_id: uuid.UUID):
    suggestions = db.scalars(select(Suggestion).where(
        Suggestion.analysis_id == analysis_id).order_by(Suggestion.id)).all()
    return {str(item.id): item.decision for item in suggestions}


def version_conflict(current_version: int):
    raise HTTPException(409, {
        'code': 'VERSION_CONFLICT', 'message': '修改稿已变化，请刷新后重试。',
        'details': {'current_version': current_version},
    })


def no_saved_revision():
    raise HTTPException(409, {
        'code': 'NO_SAVED_REVISION', 'message': '尚未保存修改稿。',
    })


def selected_saved_revision(db: Session, draft: Draft, version: int | None):
    if draft.saved_version == 0:
        no_saved_revision()
    selected_version = draft.saved_version if version is None else version
    item = db.scalar(select(SavedRevision).where(
        SavedRevision.draft_id == draft.id,
        SavedRevision.version == selected_version))
    return item if item else not_found()


def locate_original(body, original, source_segment_id, resume_snapshot):
    positions = []
    start = 0
    while True:
        position = body.find(original, start)
        if position < 0:
            break
        positions.append(position)
        start = position + max(1, len(original))
    if len(positions) == 1:
        return positions[0]
    if not positions:
        return None
    segments = [line for line in resume_snapshot.splitlines() if line.strip()]
    try:
        segment_index = int(source_segment_id[1:]) - 1
        if not 0 <= segment_index < len(segments):
            return None
    except (ValueError, IndexError):
        return None
    previous = segments[segment_index - 1] if segment_index else None
    following = segments[segment_index + 1] if segment_index + 1 < len(segments) else None
    scored = []
    for position in positions:
        left = body[:position].rstrip('\r\n')
        right = body[position + len(original):].lstrip('\r\n')
        score = int(previous is not None and left.endswith(previous))
        score += int(following is not None and right.startswith(following))
        scored.append((score, position))
    best_score = max(score for score, _ in scored)
    best = [position for score, position in scored if score == best_score]
    return best[0] if best_score > 0 and len(best) == 1 else None


def wake_worker(request: Request):
    worker = getattr(request.app.state, 'analysis_worker', None)
    if worker:
        worker.wake()


@router.post('', status_code=202)
def create_analysis(request: Request, data: AnalysisCreate,
                    idempotency_key: str = Header(min_length=1, max_length=100,
                                                  alias='Idempotency-Key'),
                    auth: AuthContext = Depends(require_csrf),
                    db: Session = Depends(get_db)):
    settings = request.app.state.settings
    if not settings.model_configured:
        raise HTTPException(503, {
            'code': 'MODEL_NOT_CONFIGURED', 'message': '模型服务尚未配置。'})
    resume = db.scalar(select(Resume).where(
        Resume.id == data.resume_id, Resume.owner_id == auth.user.id).with_for_update())
    job = db.scalar(select(Job).where(
        Job.id == data.job_id, Job.owner_id == auth.user.id).with_for_update())
    if not resume or not job:
        return not_found()
    request_fingerprint = fingerprint(resume, job)
    existing = db.scalar(select(Analysis).where(
        Analysis.owner_id == auth.user.id, Analysis.request_key == idempotency_key))
    if existing:
        if existing.request_fingerprint != request_fingerprint:
            raise HTTPException(409, {
                'code': 'IDEMPOTENCY_CONFLICT',
                'message': '该幂等键已用于不同的分析输入。',
            })
        return summary(existing)
    item = Analysis(
        owner_id=auth.user.id, resume_id=resume.id, job_id=job.id,
        resume_snapshot=resume.original_text,
        job_name_snapshot=job.name,
        job_description_snapshot=job.description,
        state='queued', attempt_no=1, request_key=idempotency_key,
        request_fingerprint=request_fingerprint, warnings=[],
    )
    db.add(item)
    try:
        db.flush()
        db.add(Draft(analysis_id=item.id, working_markdown=initial_markdown(item.resume_snapshot),
                     edit_version=1, saved_version=0))
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Analysis).where(
            Analysis.owner_id == auth.user.id, Analysis.request_key == idempotency_key))
        if not existing or existing.request_fingerprint != request_fingerprint:
            raise HTTPException(409, {
                'code': 'IDEMPOTENCY_CONFLICT',
                'message': '该幂等键已用于不同的分析输入。',
            })
        return summary(existing)
    db.refresh(item)
    wake_worker(request)
    return summary(item)


@router.get('')
def list_analyses(resume_id: uuid.UUID | None = None,
                  limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                  auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    filters = [Analysis.owner_id == auth.user.id]
    if resume_id is not None:
        filters.append(Analysis.resume_id == resume_id)
    items = db.scalars(select(Analysis).where(*filters)
                       .order_by(Analysis.created_at.desc(), Analysis.id.desc())
                       .limit(limit).offset(offset)).all()
    total = db.scalar(select(func.count()).select_from(Analysis).where(*filters))
    return {'items': [summary(item) for item in items], 'total': total,
            'limit': limit, 'offset': offset}


@router.get('/{analysis_id}')
def get_analysis(analysis_id: uuid.UUID, auth: AuthContext = Depends(current_auth),
                 db: Session = Depends(get_db)):
    item = db.scalar(select(Analysis).where(
        Analysis.id == analysis_id, Analysis.owner_id == auth.user.id))
    if not item:
        return not_found()
    requirements = db.scalars(select(AnalysisRequirement).where(
        AnalysisRequirement.analysis_id == item.id)
        .order_by(AnalysisRequirement.ordinal)).all()
    suggestions = db.scalars(select(Suggestion).join(
        AnalysisRequirement, AnalysisRequirement.id == Suggestion.requirement_id).where(
        Suggestion.analysis_id == item.id)
        .order_by(AnalysisRequirement.ordinal, Suggestion.id)).all()
    return {
        **summary(item),
        'resume_snapshot': item.resume_snapshot,
        'job_name_snapshot': item.job_name_snapshot,
        'job_description_snapshot': item.job_description_snapshot,
        'warnings': item.warnings,
        'model_metadata': item.model_metadata,
        'prompt_version': item.prompt_version,
        'validator_version': item.validator_version,
        'requirements': [{
            'id': str(value.id), 'ordinal': value.ordinal,
            'key': value.requirement_key,
            'job_requirement_quote': value.job_quote, 'job_span': value.job_span,
            'resume_evidence_quote': value.resume_quote, 'resume_span': value.resume_span,
            'match_status': value.match_status, 'reason': value.reason,
            'confirmation_questions': value.confirmation_questions,
        } for value in requirements],
        'suggestions': [suggestion_json(value) for value in suggestions],
    }


@router.get('/{analysis_id}/draft')
def get_draft(analysis_id: uuid.UUID, auth: AuthContext = Depends(current_auth),
              db: Session = Depends(get_db)):
    item = db.scalar(select(Draft).join(Analysis, Analysis.id == Draft.analysis_id).where(
        Draft.analysis_id == analysis_id, Analysis.owner_id == auth.user.id))
    return draft_json(item, current_saved_revision(db, item)) if item else not_found()


@router.put('/{analysis_id}/draft')
def save_draft(analysis_id: uuid.UUID, data: DraftSave,
               auth: AuthContext = Depends(require_csrf),
               db: Session = Depends(get_db)):
    analysis = db.scalar(select(Analysis).where(
        Analysis.id == analysis_id, Analysis.owner_id == auth.user.id).with_for_update())
    if not analysis:
        return not_found()
    draft = db.scalar(select(Draft).where(
        Draft.analysis_id == analysis.id).with_for_update())
    if not draft:
        return not_found()
    decisions = suggestion_decision_snapshot(db, analysis.id)
    latest = current_saved_revision(db, draft)
    if draft.edit_version != data.expected_edit_version:
        replay = (
            draft.edit_version == data.expected_edit_version + 1
            and latest is not None
            and latest.body_markdown == data.body_markdown
            and latest.suggestion_decisions == decisions
        )
        if replay:
            return {
                'draft': draft_json(draft, latest),
                'revision': revision_json(latest),
            }
        version_conflict(draft.edit_version)

    next_saved_version = draft.saved_version + 1
    draft.working_markdown = data.body_markdown
    draft.edit_version += 1
    draft.saved_version = next_saved_version
    draft.updated_at = utcnow()
    saved_revision = SavedRevision(
        draft_id=draft.id,
        version=next_saved_version,
        body_markdown=data.body_markdown,
        suggestion_decisions=decisions,
    )
    db.add(saved_revision)
    try:
        db.flush()
        draft_payload = draft_json(draft, saved_revision)
        revision_payload = revision_json(saved_revision)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    return {
        'draft': draft_payload,
        'revision': revision_payload,
    }


@router.get('/{analysis_id}/draft/revisions')
def list_draft_revisions(analysis_id: uuid.UUID,
                         auth: AuthContext = Depends(current_auth),
                         db: Session = Depends(get_db)):
    draft = db.scalar(select(Draft).join(Analysis, Analysis.id == Draft.analysis_id).where(
        Draft.analysis_id == analysis_id, Analysis.owner_id == auth.user.id))
    if not draft:
        return not_found()
    items = db.scalars(select(SavedRevision).where(
        SavedRevision.draft_id == draft.id)
        .order_by(SavedRevision.version.desc())).all()
    return {'items': [revision_summary(item) for item in items], 'total': len(items)}


@router.get('/{analysis_id}/draft/revisions/{version}')
def get_draft_revision(analysis_id: uuid.UUID, version: int,
                       auth: AuthContext = Depends(current_auth),
                       db: Session = Depends(get_db)):
    draft = db.scalar(select(Draft).join(Analysis, Analysis.id == Draft.analysis_id).where(
        Draft.analysis_id == analysis_id, Analysis.owner_id == auth.user.id))
    if not draft:
        return not_found()
    item = db.scalar(select(SavedRevision).where(
        SavedRevision.draft_id == draft.id, SavedRevision.version == version))
    return revision_json(item) if item else not_found()


@router.post('/{analysis_id}/draft/revisions/{version}/restore')
def restore_draft_revision(analysis_id: uuid.UUID, version: int, data: DraftRestore,
                           auth: AuthContext = Depends(require_csrf),
                           db: Session = Depends(get_db)):
    analysis = db.scalar(select(Analysis).where(
        Analysis.id == analysis_id, Analysis.owner_id == auth.user.id).with_for_update())
    if not analysis:
        return not_found()
    draft = db.scalar(select(Draft).where(
        Draft.analysis_id == analysis.id).with_for_update())
    if not draft:
        return not_found()
    if draft.edit_version != data.expected_edit_version:
        version_conflict(draft.edit_version)
    revision = db.scalar(select(SavedRevision).where(
        SavedRevision.draft_id == draft.id,
        SavedRevision.version == version))
    if not revision:
        return not_found()

    saved_revision = current_saved_revision(db, draft)
    draft.working_markdown = revision.body_markdown
    draft.edit_version += 1
    draft.updated_at = utcnow()
    try:
        db.flush()
        payload = draft_json(draft, saved_revision)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    return payload


@router.get('/{analysis_id}/preview')
def preview_saved_draft(analysis_id: uuid.UUID,
                        version: int | None = Query(default=None, ge=1),
                        auth: AuthContext = Depends(current_auth),
                        db: Session = Depends(get_db)):
    draft = db.scalar(select(Draft).join(Analysis, Analysis.id == Draft.analysis_id).where(
        Draft.analysis_id == analysis_id, Analysis.owner_id == auth.user.id))
    if not draft:
        return not_found()
    item = selected_saved_revision(db, draft, version)
    return {
        'analysis_id': str(analysis_id), 'version': item.version,
        'body_markdown': item.body_markdown, 'created_at': item.created_at,
    }


@router.get('/{analysis_id}/export.md')
def export_saved_draft(analysis_id: uuid.UUID,
                       version: int | None = Query(default=None, ge=1),
                       auth: AuthContext = Depends(current_auth),
                       db: Session = Depends(get_db)):
    draft = db.scalar(select(Draft).join(Analysis, Analysis.id == Draft.analysis_id).where(
        Draft.analysis_id == analysis_id, Analysis.owner_id == auth.user.id))
    if not draft:
        return not_found()
    item = selected_saved_revision(db, draft, version)
    return Response(
        content=item.body_markdown.encode('utf-8'),
        media_type='text/markdown; charset=utf-8',
        headers={
            'Content-Disposition': f'attachment; filename="resume-v{item.version}.md"',
            'X-Saved-Version': str(item.version),
        },
    )


@router.put('/{analysis_id}/suggestions/{suggestion_id}/decision')
def decide_suggestion(analysis_id: uuid.UUID, suggestion_id: uuid.UUID,
                      data: SuggestionDecision,
                      auth: AuthContext = Depends(require_csrf),
                      db: Session = Depends(get_db)):
    analysis = db.scalar(select(Analysis).where(
        Analysis.id == analysis_id, Analysis.owner_id == auth.user.id).with_for_update())
    if not analysis:
        return not_found()
    if analysis.state != 'succeeded':
        raise HTTPException(409, {
            'code': 'ANALYSIS_STATE_CONFLICT',
            'message': '仅已完成的分析可以处理修改建议。',
            'details': {'state': analysis.state},
        })
    suggestion = db.scalar(select(Suggestion).where(
        Suggestion.id == suggestion_id, Suggestion.analysis_id == analysis.id)
        .with_for_update())
    draft = db.scalar(select(Draft).where(Draft.analysis_id == analysis.id).with_for_update())
    if not suggestion or not draft:
        return not_found()
    if suggestion.decision == data.decision:
        return {
            'suggestion': suggestion_json(suggestion),
            'draft': draft_json(draft, current_saved_revision(db, draft)),
        }
    if suggestion.decision != 'pending':
        raise HTTPException(409, {
            'code': 'DECISION_FINAL', 'message': '该建议已经处理，决策不能更改。'})
    if data.decision == 'ignored':
        suggestion.decision = 'ignored'
        suggestion.decided_at = utcnow()
        db.commit()
        return {
            'suggestion': suggestion_json(suggestion),
            'draft': draft_json(draft, current_saved_revision(db, draft)),
        }
    if suggestion.validation_status != 'actionable' or suggestion.suggested_text is None:
        raise HTTPException(409, {
            'code': 'SUGGESTION_BLOCKED', 'message': '该建议不能直接写入修改稿。'})
    if draft.edit_version != data.expected_edit_version:
        raise HTTPException(409, {
            'code': 'VERSION_CONFLICT', 'message': '修改稿已变化，请刷新后重试。',
            'details': {'current_version': draft.edit_version},
        })
    position = locate_original(
        data.working_markdown, suggestion.original_text,
        suggestion.source_segment_id, analysis.resume_snapshot)
    if position is None:
        raise HTTPException(409, {
            'code': 'SUGGESTION_CONFLICT',
            'message': '无法唯一定位原文，请检查当前修改稿后重试。',
        })
    draft.working_markdown = (
        data.working_markdown[:position] + suggestion.suggested_text +
        data.working_markdown[position + len(suggestion.original_text):])
    draft.edit_version += 1
    draft.updated_at = utcnow()
    suggestion.decision = 'accepted'
    suggestion.decided_at = utcnow()
    db.commit()
    return {
        'suggestion': suggestion_json(suggestion),
        'draft': draft_json(draft, current_saved_revision(db, draft)),
    }


@router.post('/{analysis_id}/retry', status_code=202)
def retry_analysis(request: Request, analysis_id: uuid.UUID, data: AnalysisRetry,
                   auth: AuthContext = Depends(require_csrf), db: Session = Depends(get_db)):
    if not request.app.state.settings.model_configured:
        raise HTTPException(503, {
            'code': 'MODEL_NOT_CONFIGURED', 'message': '模型服务尚未配置。'})
    updated_id = db.scalar(update(Analysis).where(
        Analysis.id == analysis_id,
        Analysis.owner_id == auth.user.id,
        Analysis.state == 'failed',
        Analysis.attempt_no == data.expected_attempt_no,
    ).values(
        state='queued', attempt_no=Analysis.attempt_no + 1,
        started_at=None, finished_at=None, lease_until=None,
        error_code=None, error_message=None, model_metadata=None, warnings=[],
    ).returning(Analysis.id))
    if updated_id is None:
        item = db.scalar(select(Analysis).where(
            Analysis.id == analysis_id, Analysis.owner_id == auth.user.id))
        if not item:
            return not_found()
        raise HTTPException(409, {
            'code': 'ANALYSIS_STATE_CONFLICT',
            'message': '任务状态或尝试次数已变化，请刷新后重试。',
            'details': {'state': item.state, 'attempt_no': item.attempt_no},
        })
    db.execute(delete(AnalysisRequirement).where(
        AnalysisRequirement.analysis_id == analysis_id))
    db.commit()
    item = db.get(Analysis, updated_id)
    wake_worker(request)
    return summary(item)
