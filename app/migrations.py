"""Minimal migration runner.

Why not Alembic: the schema is written by hand with specific constraints
(composite UNIQUE keys, ENUMs, JSON columns) that Alembic's autogenerate
tends to reinterpret badly. Here the .sql files are the source of truth, they
are applied once each in order, and the `schema_migrations` table keeps count.

Adding a migration = create `db/migrations/00N_name.sql`. That is all.
"""

import logging
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.db import engine

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"


def wait_for_db(max_attempts: int = 30, delay: float = 2.0) -> None:
    """The app container can start before MySQL accepts connections."""
    for attempt in range(1, max_attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            log.info("Database reachable.")
            return
        except OperationalError as exc:
            if attempt == max_attempts:
                raise RuntimeError(
                    f"Database unreachable after {max_attempts} attempts"
                ) from exc
            log.warning("Database not ready (attempt %s), retrying...", attempt)
            time.sleep(delay)


def _split_statements(sql: str) -> list[str]:
    """Split on ';' while ignoring the ones inside strings or comments."""
    statements: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    in_line_comment = False

    for i, ch in enumerate(sql):
        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            continue

        if quote:
            buf.append(ch)
            # backslash escape: skip the next character
            if ch == "\\":
                continue
            if ch == quote:
                quote = None
            continue

        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            continue

        if ch == "-" and sql[i : i + 2] == "--":
            in_line_comment = True
            buf.append(ch)
            continue

        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            continue

        buf.append(ch)

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _applied_versions(conn) -> set[str]:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version    VARCHAR(255) NOT NULL,
              applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (version)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )
    rows = conn.execute(text("SELECT version FROM schema_migrations"))
    return {row[0] for row in rows}


def run_migrations() -> None:
    if not MIGRATIONS_DIR.is_dir():
        raise RuntimeError(f"Migrations directory not found: {MIGRATIONS_DIR}")

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        log.warning("No migrations found in %s", MIGRATIONS_DIR)
        return

    with engine.begin() as conn:
        applied = _applied_versions(conn)

    for path in files:
        version = path.stem
        if version in applied:
            continue

        log.info("Applying migration %s", version)
        sql = path.read_text(encoding="utf-8")

        # One transaction per migration: if a statement fails, the migration
        # is not recorded. (Note: MySQL commits implicitly on DDL, so the
        # rollback only covers data — one more reason to keep schema and seed
        # in separate files.)
        with engine.begin() as conn:
            for stmt in _split_statements(sql):
                conn.execute(text(stmt))
            conn.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:v)"),
                {"v": version},
            )

        log.info("Migration %s applied.", version)
