import hashlib
import hmac
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import LoginSession, User, utcnow

COOKIE_NAME = 'resume_session'
hasher = PasswordHasher()
# Perform a hash verification for unknown users too, without storing a test password here.
DUMMY_HASH = hasher.hash('dummy-password-not-used-to-log-in')


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_for(token: str) -> str:
    return digest('csrf:' + token)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def require_origin(request: Request):
    if request.headers.get('origin') not in request.app.state.settings.origins:
        raise HTTPException(403, {'code': 'ORIGIN_DENIED', 'message': '请求来源不被允许。'})


@dataclass
class AuthContext:
    user: User
    session: LoginSession
    csrf_token: str


def current_auth(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    token = request.cookies.get(COOKIE_NAME, '')
    if not 20 <= len(token) <= 128:
        raise HTTPException(401, {'code': 'NOT_AUTHENTICATED', 'message': '请先登录。'})
    session = db.scalar(select(LoginSession).where(
        LoginSession.token_hash == digest(token),
        LoginSession.revoked_at.is_(None),
        LoginSession.expires_at > utcnow(),
    ))
    user = db.get(User, session.user_id) if session else None
    if not user:
        raise HTTPException(401, {'code': 'SESSION_EXPIRED', 'message': '登录已失效，请重新登录。'})
    return AuthContext(user, session, csrf_for(token))


def current_user(auth: AuthContext = Depends(current_auth)) -> User:
    """Backend dependency for all later protected resource routes."""
    return auth.user


def require_csrf(request: Request, auth: AuthContext = Depends(current_auth)):
    require_origin(request)
    supplied = request.headers.get('x-csrf-token', '')
    if not hmac.compare_digest(digest(supplied), auth.session.csrf_token_hash):
        raise HTTPException(403, {'code': 'CSRF_REJECTED', 'message': '请求校验失败，请刷新页面后重试。'})
    return auth
