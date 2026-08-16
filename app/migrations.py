"""Runner di migrazioni minimale.

Perché non Alembic: lo schema è scritto a mano con vincoli specifici (UNIQUE
compositi, ENUM, colonne JSON) che l'autogenerate di Alembic tende a
reinterpretare male. Qui i file .sql sono la verità, vengono applicati in
ordine una sola volta, e la tabella `schema_migrations` tiene il conto.

Aggiungere una migrazione = creare `db/migrations/00N_nome.sql`. Fine.
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
    """Il container app può partire prima che MySQL accetti connessioni."""
    for attempt in range(1, max_attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            log.info("Database raggiungibile.")
            return
        except OperationalError as exc:
            if attempt == max_attempts:
                raise RuntimeError(
                    f"Database irraggiungibile dopo {max_attempts} tentativi"
                ) from exc
            log.warning("Database non pronto (tentativo %s), riprovo...", attempt)
            time.sleep(delay)


def _split_statements(sql: str) -> list[str]:
    """Divide su ';' ignorando quelli dentro stringhe o commenti."""
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
            # backslash-escape: salta il carattere successivo
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
        raise RuntimeError(f"Cartella migrazioni non trovata: {MIGRATIONS_DIR}")

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        log.warning("Nessuna migrazione trovata in %s", MIGRATIONS_DIR)
        return

    with engine.begin() as conn:
        applied = _applied_versions(conn)

    for path in files:
        version = path.stem
        if version in applied:
            continue

        log.info("Applico migrazione %s", version)
        sql = path.read_text(encoding="utf-8")

        # Una transazione per migrazione: se una statement fallisce, l'intera
        # migrazione non viene registrata. (Nota: MySQL fa commit implicito
        # sui DDL, quindi il rollback copre solo i dati — motivo in più per
        # tenere schema e seed in file separati.)
        with engine.begin() as conn:
            for stmt in _split_statements(sql):
                conn.execute(text(stmt))
            conn.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:v)"),
                {"v": version},
            )

        log.info("Migrazione %s applicata.", version)
