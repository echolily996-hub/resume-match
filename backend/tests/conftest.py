import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.config import Settings
from app.main import create_app
from app.seed import seed_users


@pytest.fixture
def engine(tmp_path, monkeypatch):
    # Portable contract tests only. This is not PostgreSQL/Docker acceptance.
    url = 'sqlite:///' + (tmp_path / 'auth.db').as_posix()
    monkeypatch.setenv('DATABASE_URL', url)
    for key in ('SEED_USER_A', 'SEED_PASSWORD_A', 'SEED_USER_B', 'SEED_PASSWORD_B'):
        monkeypatch.delenv(key, raising=False)
    command.upgrade(Config('alembic.ini'), 'head')
    engine = create_engine(url, connect_args={'check_same_thread': False})
    seed_users(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def app(engine):
    return create_app(Settings(str(engine.url), (
        'http://testserver',
        'http://localhost:5173',
    )), engine)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def login(client, username='demo_a', password='Demo-A-2026!'):
    return client.post('/api/v1/auth/login', headers={'Origin': 'http://testserver'},
                       json={'username': username, 'password': password})
