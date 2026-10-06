import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import hasher
from .config import Settings
from .db import make_engine
from .models import User


def seed_users(engine):
    accounts = [
        (os.environ.get('SEED_USER_A', 'demo_a'), os.environ.get('SEED_PASSWORD_A', 'Demo-A-2026!')),
        (os.environ.get('SEED_USER_B', 'demo_b'), os.environ.get('SEED_PASSWORD_B', 'Demo-B-2026!')),
    ]
    if accounts[0][0] == accounts[1][0] or any(not u.strip() or not p for u, p in accounts):
        raise ValueError('Two distinct seed accounts with nonempty passwords are required')
    if any(len(u) > 100 or len(p) > 256 for u, p in accounts):
        raise ValueError('Seed credentials exceed field limits')
    with Session(engine) as db, db.begin():
        for username, password in accounts:
            if db.scalar(select(User).where(User.username == username)) is None:
                db.add(User(username=username, password_hash=hasher.hash(password)))
    # Existing accounts are intentionally not reset on subsequent container starts.


if __name__ == '__main__':
    engine = make_engine(Settings.from_env().database_url)
    seed_users(engine)
    engine.dispose()
    print('Seed initialization completed (existing accounts preserved).')
