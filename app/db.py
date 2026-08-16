"""Database connection."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    # pool_pre_ping: tests a pooled connection before reusing it. Without it,
    # the first query after a long idle period fails with
    # "MySQL server has gone away".
    pool_pre_ping=True,
    # MySQL closes idle connections after 8 hours (wait_timeout): we recycle
    # them first.
    pool_recycle=3600,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
