"""Database engine, session factory, the reusable `get_db` dependency and a safe commit helper."""
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings


class Base(DeclarativeBase):
    """Parent class of all SQLAlchemy models."""


def _create_engine():
    url = settings.sqlalchemy_url
    if url.startswith("sqlite"):  # only used by the automated tests / local demos
        kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
        eng = create_engine(url, **kwargs)

        @event.listens_for(eng, "connect")
        def _enable_foreign_keys(dbapi_connection, _):  # MySQL enforces foreign keys; make SQLite do the same
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        return eng
    # pool_pre_ping drops dead connections, pool_recycle avoids MySQL "gone away" errors
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600)


engine = _create_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed (and rolled back if not committed)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def commit(db: Session) -> None:
    """Commit; on any database error roll back so the session stays usable, then re-raise."""
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
