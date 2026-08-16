"""FastAPI entrypoint."""

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
log = logging.getLogger("vaultflow")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting: waiting for the database...")
    wait_for_db()
    run_migrations()
    if not password_is_configured():
        log.warning(
            "APP_PASSWORD is not set (or left at an example value): sign-in is "
            "BLOCKED until you change it in the .env file. Do not deploy in "
            "this state."
        )
    log.info("Ready.")
    yield


app = FastAPI(
    title="Vault Flow",
    description="Self-hosted personal expense dashboard.",
    version="0.1.0",
    lifespan=lifespan,
)

# The auth routes are public: they are the door. Everything else is locked.
app.include_router(auth.router, prefix="/api")

protected = [Depends(require_auth)]
app.include_router(accounts.router, prefix="/api", dependencies=protected)
app.include_router(imports.router, prefix="/api", dependencies=protected)
app.include_router(transactions.router, prefix="/api", dependencies=protected)
app.include_router(rules.router, prefix="/api", dependencies=protected)
app.include_router(stats.router, prefix="/api", dependencies=protected)
app.include_router(detect.router, prefix="/api", dependencies=protected)


class RevalidatingStaticFiles(StaticFiles):
    """Static files served with `Cache-Control: no-cache`.

    Without an explicit header the browser applies *heuristic caching*: it
    keeps files for a fraction of their lifetime without asking the server. In
    development that means edits that never show up; in production, users
    stuck on old JavaScript after an update.

    `no-cache` does not mean "do not cache": it means "ask me before reusing
    it". With the ETag the answer is an empty 304, so the cost is one minimal
    request and the files stay in the local cache.
    Offline is the service worker's job, not this cache's.
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
    """The service worker must live at the root to be able to control "/".

    Served from /static it could not intercept navigation to the home page.
    """
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            # The service worker decides what the user sees: if it stays
            # cached, so does the wrong strategy you were trying to fix. It
            # must always be revalidated.
            "Cache-Control": "no-cache",
        },
    )


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Checks that the app and the database can talk to each other.

    **Public and deliberately silent**: it exists for monitoring, so it must
    not reveal the user's name or how many transactions are inside. The counts
    live in `/api/health/details`, behind authentication.
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
