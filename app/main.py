"""Entrypoint FastAPI.

Fase 1: l'app si avvia, aspetta MySQL, applica le migrazioni ed espone
/health per verificare che tutta la catena funzioni. Niente di più: le
funzionalità arrivano dalla Fase 2.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import password_is_configured, require_auth
from app.db import get_db
from app.migrations import run_migrations, wait_for_db
from app.models import Account, Category, Setting, Transaction
from app.routers import accounts, auth, detect, imports, rules, stats, transactions

STATIC_DIR = Path(__file__).resolve().parent / "static"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("spese")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Avvio: attendo il database...")
    wait_for_db()
    run_migrations()
    if not password_is_configured():
        log.warning(
            "APP_PASSWORD non impostata (o lasciata a quella di esempio): "
            "l'accesso è BLOCCATO finché non la cambi nel file .env. "
            "Non deployare in questo stato."
        )
    log.info("Pronto.")
    yield


app = FastAPI(
    title="Dashboard Spese",
    description="Dashboard self-hosted per le spese personali.",
    version="0.1.0",
    lifespan=lifespan,
)


# Le rotte di accesso sono pubbliche: sono la porta. Tutto il resto è chiuso.
app.include_router(auth.router, prefix="/api")

protected = [Depends(require_auth)]
app.include_router(accounts.router, prefix="/api", dependencies=protected)
app.include_router(imports.router, prefix="/api", dependencies=protected)
app.include_router(transactions.router, prefix="/api", dependencies=protected)
app.include_router(rules.router, prefix="/api", dependencies=protected)
app.include_router(stats.router, prefix="/api", dependencies=protected)
app.include_router(detect.router, prefix="/api", dependencies=protected)

class RevalidatingStaticFiles(StaticFiles):
    """File statici con `Cache-Control: no-cache`.

    Senza un header esplicito il browser applica la *cache euristica*: tiene i
    file per una frazione del loro tempo di vita, senza chiedere nulla al
    server. In sviluppo significa modifiche che non si vedono; in produzione
    utenti bloccati sul JavaScript vecchio dopo un aggiornamento.

    `no-cache` non vuol dire "non mettere in cache": vuol dire "chiedimi prima
    di riusarlo". Con l'ETag la risposta è un 304 vuoto, quindi il costo è una
    richiesta minima e i file restano in cache locale.
    L'offline è compito del service worker, non di questa cache.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", RevalidatingStaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Il service worker deve stare alla radice per poter controllare "/".

    Servito da /static non potrebbe intercettare la navigazione sulla home.
    """
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            # Il service worker decide cosa vede l'utente: se resta in cache,
            # resta in cache anche la strategia sbagliata che si voleva
            # correggere. Va sempre rivalidato.
            "Cache-Control": "no-cache",
        },
    )


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Verifica che app e database si parlino.

    **Pubblica e volutamente muta**: serve al monitoraggio, quindi non deve
    rivelare né il nome dell'utente né quanti movimenti ci sono dentro. I
    conteggi stanno in `/api/stats/*`, dietro autenticazione.
    """
    db.execute(select(1))
    return {"status": "ok"}


@app.get("/api/health/details", dependencies=[Depends(require_auth)])
def health_details(db: Session = Depends(get_db)) -> dict:
    return {
        "status": "ok",
        "display_name": db.scalar(
            select(Setting.value).where(Setting.setting_key == "display_name")
        ),
        "counts": {
            "accounts": db.scalar(select(func.count()).select_from(Account)),
            "categories": db.scalar(select(func.count()).select_from(Category)),
            "transactions": db.scalar(select(func.count()).select_from(Transaction)),
        },
    }
