from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
_engine: Engine | None = None


def init_engine(url: str | None = None) -> Engine:
    global _engine
    url = url or get_settings().database_url
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(pool_size=10, max_overflow=10)
    _engine = create_engine(url, **kwargs)
    SessionLocal.configure(bind=_engine)
    return _engine


def get_engine() -> Engine:
    return _engine or init_engine()


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    get_engine()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
