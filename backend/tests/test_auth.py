from datetime import timedelta

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth import COOKIE_NAME, current_user, digest, verify_password
from app.db import get_db
from app.models import LoginSession, User, utcnow
from app.seed import seed_users
from conftest import login


def test_two_accounts_seeded_and_idempotent(engine):
    with Session(engine) as db:
        before = {u.username: (u.id, u.password_hash) for u in db.scalars(select(User))}
    seed_users(engine)
    with Session(engine) as db:
        after = {u.username: (u.id, u.password_hash) for u in db.scalars(select(User))}
    assert before == after
    assert set(after) == {'demo_a', 'demo_b'}
    assert after['demo_a'][0] != after['demo_b'][0]
    assert after['demo_a'][1].startswith('$argon2id$')
    assert verify_password(after['demo_a'][1], 'Demo-A-2026!')


def test_unauthenticated_and_fake_cookie(client):
    assert client.get('/api/v1/auth/me').status_code == 401
    client.cookies.set(COOKIE_NAME, 'x' * 43)
    assert client.get('/api/v1/auth/me').status_code == 401


def test_login_and_restore_session(client, engine):
    response = login(client)
    assert response.status_code == 200
    assert response.json()['user']['username'] == 'demo_a'
    cookie = response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=lax' in cookie
    token = client.cookies.get(COOKIE_NAME)
    me = client.get('/api/v1/auth/me')
    assert me.status_code == 200
    assert me.json() == response.json()
    with Session(engine) as db:
        session = db.scalar(select(LoginSession))
        assert session.token_hash == digest(token)
        assert session.token_hash != token
        assert session.csrf_token_hash != response.json()['csrf_token']
    with TestClient(client.app) as fresh:
        fresh.cookies.set(COOKIE_NAME, token)
        assert fresh.get('/api/v1/auth/me').json() == me.json()


def test_invalid_credentials_never_create_session(client, engine):
    for username, password in [('demo_a', 'wrong'), ('missing', 'wrong')]:
        result = login(client, username, password)
        assert result.status_code == 401
        assert result.json()['error']['code'] == 'INVALID_CREDENTIALS'
        assert 'set-cookie' not in result.headers
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(LoginSession)) == 0


def test_logout_revokes_token_and_clears_cookie(client):
    info = login(client).json()
    token = client.cookies.get(COOKIE_NAME)
    result = client.post('/api/v1/auth/logout', headers={
        'Origin': 'http://testserver', 'X-CSRF-Token': info['csrf_token']})
    assert result.status_code == 204
    assert 'max-age=0' in result.headers['set-cookie'].lower()
    assert client.get('/api/v1/auth/me').status_code == 401
    client.cookies.set(COOKIE_NAME, token)
    assert client.get('/api/v1/auth/me').status_code == 401


def test_csrf_and_origin_rejected(client):
    info = login(client).json()
    assert client.post('/api/v1/auth/logout', headers={'Origin': 'http://testserver'}).status_code == 403
    assert client.post('/api/v1/auth/logout', headers={
        'Origin': 'http://evil.test', 'X-CSRF-Token': info['csrf_token']}).status_code == 403
    assert client.get('/api/v1/auth/me').status_code == 200
    assert client.post('/api/v1/auth/login', headers={'Origin': 'http://evil.test'},
                       json={'username': 'demo_a', 'password': 'Demo-A-2026!'}).status_code == 403


def test_vite_localhost_origin_allowed_but_nearby_origins_rejected(client):
    accepted = client.post('/api/v1/auth/login', headers={'Origin': 'http://localhost:5173'},
                           json={'username': 'demo_a', 'password': 'Demo-A-2026!'})
    assert accepted.status_code == 200
    for origin in ('http://127.0.0.1:5173', 'http://localhost:5174', 'https://localhost:5173'):
        rejected = client.post('/api/v1/auth/login', headers={'Origin': origin},
                               json={'username': 'demo_a', 'password': 'Demo-A-2026!'})
        assert rejected.status_code == 403
        assert rejected.json()['error']['code'] == 'ORIGIN_DENIED'


def test_expired_session(client, engine):
    login(client)
    with Session(engine) as db:
        session = db.scalar(select(LoginSession))
        session.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get('/api/v1/auth/me').status_code == 401


def test_relogin_rotates_and_revokes_previous_session(client):
    login(client)
    old_token = client.cookies.get(COOKIE_NAME)
    login(client)
    assert client.cookies.get(COOKIE_NAME) != old_token
    client.cookies.set(COOKIE_NAME, old_token)
    assert client.get('/api/v1/auth/me').status_code == 401


def test_two_accounts_identity_isolation(app):
    with TestClient(app) as a, TestClient(app) as b:
        assert login(a).status_code == 200
        assert login(b, 'demo_b', 'Demo-B-2026!').status_code == 200
        aa = a.get('/api/v1/auth/me').json()['user']
        bb = b.get('/api/v1/auth/me').json()['user']
        assert aa['username'] == 'demo_a'
        assert bb['username'] == 'demo_b'
        assert aa['id'] != bb['id']


def test_backend_dependency_protects_routes(app):
    # Test-only route proves the guard works without adding a product feature.
    @app.get('/test/protected')
    def protected(user=Depends(current_user)):
        return {'username': user.username}
    with TestClient(app) as client:
        assert client.get('/test/protected').status_code == 401
        login(client)
        assert client.get('/test/protected').json() == {'username': 'demo_a'}


def test_validation_does_not_echo_password(client):
    secret = 'never-echo-this-password'
    result = client.post('/api/v1/auth/login', headers={'Origin': 'http://testserver'},
                         json={'username': ' ', 'password': secret, 'owner_id': 'spoof'})
    assert result.status_code == 422
    assert secret not in result.text
    assert 'x-request-id' in result.headers


def test_health_and_phase2_scope_routes(client):
    assert client.get('/api/v1/health').json() == {'status': 'ok'}
    routes = client.get('/openapi.json').json()['paths']
    assert set(routes) == {
        '/api/v1/health', '/api/v1/auth/login', '/api/v1/auth/me', '/api/v1/auth/logout',
        '/api/v1/resumes', '/api/v1/resumes/{resume_id}',
        '/api/v1/jobs', '/api/v1/jobs/{job_id}',
        '/api/v1/analyses', '/api/v1/analyses/{analysis_id}',
            '/api/v1/analyses/{analysis_id}/retry',
            '/api/v1/analyses/{analysis_id}/draft',
            '/api/v1/analyses/{analysis_id}/draft/revisions',
            '/api/v1/analyses/{analysis_id}/draft/revisions/{version}',
            '/api/v1/analyses/{analysis_id}/draft/revisions/{version}/restore',
            '/api/v1/analyses/{analysis_id}/preview',
            '/api/v1/analyses/{analysis_id}/export.md',
            '/api/v1/analyses/{analysis_id}/suggestions/{suggestion_id}/decision',
        }


def test_database_failure_never_returns_success_or_secret(app):
    class BrokenDB:
        def scalar(self, query):
            raise OperationalError('SELECT', {'password': 'secret-db-value'}, Exception('offline'))

    def broken_db():
        yield BrokenDB()

    app.dependency_overrides[get_db] = broken_db
    with TestClient(app) as client:
        response = login(client)
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'PERSISTENCE_FAILED'
        assert 'secret-db-value' not in response.text
        assert 'set-cookie' not in response.headers
