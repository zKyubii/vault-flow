"""Accesso: login, logout, stato."""

import logging

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    COOKIE_NAME,
    SESSION_DAYS,
    check_password,
    clear_failures,
    get_secret,
    make_token,
    password_is_configured,
    rate_limited,
    record_failure,
    reset_secret,
    verify_token,
)
from app.config import get_settings
from app.db import get_db
from app.models import Setting
from app.schemas import Message

log = logging.getLogger("spese.auth")
router = APIRouter(tags=["accesso"])


def _set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        # In HTTPS deve stare a True. Non è il valore predefinito perché in
        # sviluppo si gira su http://localhost e un cookie `secure` non
        # verrebbe mai inviato, rendendo il login apparentemente rotto.
        secure=settings.cookie_secure,
        path="/",
    )


@router.get("/auth/me")
def me(
    db: Session = Depends(get_db),
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    """Stato dell'accesso. Sempre raggiungibile: è ciò che la PWA interroga
    all'avvio per decidere se mostrare il login o la dashboard."""
    authenticated = verify_token(get_secret(db), session)
    display_name = None
    if authenticated:
        display_name = db.scalar(
            select(Setting.value).where(Setting.setting_key == "display_name")
        )
    return {
        "authenticated": authenticated,
        "display_name": display_name,
        # se il .env è ancora quello di esempio l'interfaccia deve dirlo,
        # invece di far sbattere l'utente su una password che non funziona
        "password_configured": password_is_configured(),
    }


@router.post("/auth/login")
def login(
    request: Request,
    response: Response,
    password: str = Body(..., embed=True),
    db: Session = Depends(get_db),
):
    wait = rate_limited(request)
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Troppi tentativi falliti. Riprova fra {wait} secondi.",
        )

    if not password_is_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Nessuna password impostata: modifica APP_PASSWORD nel file .env e "
            "riavvia il container.",
        )

    if not check_password(password):
        record_failure(request)
        log.warning("Tentativo di accesso fallito da %s", request.client.host if request.client else "?")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Password errata")

    clear_failures(request)
    _set_cookie(response, make_token(get_secret(db)))
    return {"detail": "Accesso effettuato"}


@router.post("/auth/logout", response_model=Message)
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return Message(detail="Uscita effettuata")


@router.post("/auth/logout-everywhere", response_model=Message)
def logout_everywhere(response: Response, db: Session = Depends(get_db)):
    """Rigenera il segreto di firma: invalida le sessioni su tutti i
    dispositivi. Serve se si perde il telefono."""
    reset_secret(db)
    response.delete_cookie(COOKIE_NAME, path="/")
    return Message(detail="Tutte le sessioni sono state chiuse")
