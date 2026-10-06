from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


def make_engine(url):
    return create_engine(url, pool_pre_ping=True)


def get_db(request: Request):
    with Session(request.app.state.engine) as session:
        yield session
