import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .auth import (COOKIE_NAME, DUMMY_HASH, csrf_for, current_auth, digest,
                   hasher, require_csrf, require_origin, verify_password)
from .analyses import router as analyses_router
from .analysis_service import AnalysisWorker
from .config import Settings
from .db import get_db, make_engine
from .models import LoginSession, User, utcnow
from .resources import router as resources_router


class LoginInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)

    @field_validator('username')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('登录名不能为空')
        return value.strip()


def create_app(settings=None, engine=None):
    resolved_settings = settings or Settings.from_env()
    resolved_engine = engine if engine is not None else make_engine(resolved_settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        if app.state.settings.analysis_worker_enabled and app.state.settings.model_configured:
            app.state.analysis_worker = AnalysisWorker(app.state.engine, app.state.settings)
            app.state.analysis_worker.start()
        try:
            yield
        finally:
            worker = getattr(app.state, 'analysis_worker', None)
            if worker:
                worker.stop()

    app = FastAPI(title='简历与岗位匹配助手 · Phase 3', docs_url=None, redoc_url=None,
                  lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.engine = resolved_engine

    @app.middleware('http')
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def error(request, status, code, message, details=None):
        return JSONResponse(status_code=status, content={'error': {
            'code': code, 'message': message, 'details': details,
            'request_id': request.state.request_id,
        }})

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        detail = exc.detail if isinstance(exc.detail, dict) else {'code': 'REQUEST_FAILED', 'message': str(exc.detail)}
        return error(request, exc.status_code, detail['code'], detail['message'], detail.get('details'))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's raw error input can include passwords. Return field information only.
        details = [{'field': '.'.join(str(x) for x in e['loc']), 'type': e['type']} for e in exc.errors()]
        return error(request, 422, 'INVALID_INPUT', '请检查请求字段、长度及格式。', details)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return error(request, 503, 'PERSISTENCE_FAILED', '数据库暂时不可用，请稍后重试。')

    @app.get('/api/v1/health')
    def health(db: Session = Depends(get_db)):
        db.execute(text('SELECT 1'))
        return {'status': 'ok'}

    @app.post('/api/v1/auth/login', dependencies=[Depends(require_origin)])
    def login(data: LoginInput, response: Response, request: Request, db: Session = Depends(get_db)):
        user = db.scalar(select(User).where(User.username == data.username))
        valid = verify_password(user.password_hash if user else DUMMY_HASH, data.password)
        if not user or not valid:
            raise HTTPException(401, {'code': 'INVALID_CREDENTIALS', 'message': '登录名或密码错误。'})
        token = secrets.token_urlsafe(32)
        csrf = csrf_for(token)
        old = request.cookies.get(COOKIE_NAME)
        if old:
            previous = db.scalar(select(LoginSession).where(LoginSession.token_hash == digest(old)))
            if previous:
                previous.revoked_at = utcnow()
        expires = utcnow() + timedelta(hours=app.state.settings.session_hours)
        db.add(LoginSession(user_id=user.id, token_hash=digest(token), csrf_token_hash=digest(csrf), expires_at=expires))
        if hasher.check_needs_rehash(user.password_hash):
            user.password_hash = hasher.hash(data.password)
        db.commit()
        response.set_cookie(COOKIE_NAME, token, httponly=True, secure=app.state.settings.cookie_secure,
                            samesite='lax', path='/', max_age=app.state.settings.session_hours * 3600)
        return {'user': {'id': str(user.id), 'username': user.username}, 'csrf_token': csrf}

    @app.get('/api/v1/auth/me')
    def me(auth=Depends(current_auth)):
        return {'user': {'id': str(auth.user.id), 'username': auth.user.username}, 'csrf_token': auth.csrf_token}

    @app.post('/api/v1/auth/logout', status_code=204)
    def logout(response: Response, auth=Depends(require_csrf), db: Session = Depends(get_db)):
        auth.session.revoked_at = utcnow()
        db.commit()
        response.delete_cookie(COOKIE_NAME, path='/', httponly=True,
                               secure=app.state.settings.cookie_secure, samesite='lax')
        response.status_code = 204
        return response

    app.include_router(resources_router)
    app.include_router(analyses_router)

    return app


app = create_app()
