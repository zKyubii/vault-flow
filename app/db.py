"""Connessione al database."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    # pool_pre_ping: prima di riusare una connessione dal pool la testa.
    # Senza, la prima query dopo una pausa lunga fallisce con
    # "MySQL server has gone away".
    pool_pre_ping=True,
    # MySQL chiude le connessioni inattive dopo 8 ore (wait_timeout):
    # le ricicliamo prima noi.
    pool_recycle=3600,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """Dipendenza FastAPI: una sessione per richiesta, chiusa sempre."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
