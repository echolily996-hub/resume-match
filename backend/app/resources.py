import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .auth import AuthContext, current_auth, require_csrf
from .db import get_db
from .models import Job, Resume, utcnow

router = APIRouter(prefix='/api/v1')


def not_found():
    raise HTTPException(404, {'code': 'RESOURCE_NOT_FOUND', 'message': '记录不存在。'})


def required_text(value: str, label: str, preserve: bool = False) -> str:
    if not value.strip():
        raise ValueError(f'{label}不能为空')
    return value if preserve else value.strip()


class ResumeCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=200)
    original_text: str = Field(min_length=1, max_length=30_000)

    @field_validator('title')
    @classmethod
    def title_required(cls, value):
        return required_text(value, '简历标题')

    @field_validator('original_text')
    @classmethod
    def body_required(cls, value):
        return required_text(value, '简历正文', preserve=True)


class JobCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=20_000)

    @field_validator('name')
    @classmethod
    def name_required(cls, value):
        return required_text(value, '岗位名称')

    @field_validator('description')
    @classmethod
    def description_required(cls, value):
        return required_text(value, '岗位描述', preserve=True)


class JobUpdate(JobCreate):
    expected_version: int = Field(ge=1)


def resume_json(item: Resume, include_text=True):
    result = {'id': str(item.id), 'title': item.title, 'created_at': item.created_at}
    if include_text:
        result['original_text'] = item.original_text
    return result


def job_json(item: Job, include_description=True):
    result = {
        'id': str(item.id), 'name': item.name, 'version': item.version,
        'created_at': item.created_at, 'updated_at': item.updated_at,
    }
    if include_description:
        result['description'] = item.description
    return result


@router.get('/resumes')
def list_resumes(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                 auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    base = select(Resume).where(Resume.owner_id == auth.user.id)
    items = db.scalars(base.order_by(Resume.created_at.desc(), Resume.id.desc())
                       .limit(limit).offset(offset)).all()
    total = db.scalar(select(func.count()).select_from(Resume)
                      .where(Resume.owner_id == auth.user.id))
    return {'items': [resume_json(item, False) for item in items], 'total': total,
            'limit': limit, 'offset': offset}


@router.post('/resumes', status_code=201)
def create_resume(data: ResumeCreate, auth: AuthContext = Depends(require_csrf),
                  db: Session = Depends(get_db)):
    item = Resume(owner_id=auth.user.id, title=data.title, original_text=data.original_text)
    db.add(item)
    db.commit()
    db.refresh(item)
    return resume_json(item)


@router.get('/resumes/{resume_id}')
def get_resume(resume_id: uuid.UUID, auth: AuthContext = Depends(current_auth),
               db: Session = Depends(get_db)):
    item = db.scalar(select(Resume).where(Resume.id == resume_id,
                                          Resume.owner_id == auth.user.id))
    return resume_json(item) if item else not_found()


@router.get('/jobs')
def list_jobs(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
              auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    base = select(Job).where(Job.owner_id == auth.user.id)
    items = db.scalars(base.order_by(Job.created_at.desc(), Job.id.desc())
                       .limit(limit).offset(offset)).all()
    total = db.scalar(select(func.count()).select_from(Job)
                      .where(Job.owner_id == auth.user.id))
    return {'items': [job_json(item, False) for item in items], 'total': total,
            'limit': limit, 'offset': offset}


@router.post('/jobs', status_code=201)
def create_job(data: JobCreate, auth: AuthContext = Depends(require_csrf),
               db: Session = Depends(get_db)):
    item = Job(owner_id=auth.user.id, name=data.name, description=data.description)
    db.add(item)
    db.commit()
    db.refresh(item)
    return job_json(item)


@router.get('/jobs/{job_id}')
def get_job(job_id: uuid.UUID, auth: AuthContext = Depends(current_auth),
            db: Session = Depends(get_db)):
    item = db.scalar(select(Job).where(Job.id == job_id, Job.owner_id == auth.user.id))
    return job_json(item) if item else not_found()


@router.put('/jobs/{job_id}')
def update_job(job_id: uuid.UUID, data: JobUpdate, auth: AuthContext = Depends(require_csrf),
               db: Session = Depends(get_db)):
    updated_id = db.scalar(update(Job).where(
        Job.id == job_id,
        Job.owner_id == auth.user.id,
        Job.version == data.expected_version,
    ).values(
        name=data.name,
        description=data.description,
        version=Job.version + 1,
        updated_at=utcnow(),
    ).returning(Job.id))
    if updated_id is None:
        current_version = db.scalar(select(Job.version).where(
            Job.id == job_id, Job.owner_id == auth.user.id))
        if current_version is None:
            return not_found()
        raise HTTPException(409, {
            'code': 'VERSION_CONFLICT',
            'message': '岗位已被更新，请刷新后重试。',
            'details': {'current_version': current_version},
        })
    db.commit()
    item = db.scalar(select(Job).where(Job.id == updated_id, Job.owner_id == auth.user.id))
    return job_json(item)
