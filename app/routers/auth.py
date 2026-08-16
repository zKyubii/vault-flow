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
router = APIRouter(tags=["auth"])


def _set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        # Must be True over HTTPS. It is not the default because development
        # runs on http://localhost, where a `secure` cookie would never be
        # sent, making login look broken.
        secure=settings.cookie_secure,
        path="/",
    )


@router.get("/auth/me")
def me(
    db: Session = Depends(get_db),
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    """Sign-in state. Always reachable: this is what the PWA asks on startup
    to decide whether to show the login or the dashboard."""
    authenticated = verify_token(get_secret(db), session)
    display_name = None
    if authenticated:
        display_name = db.scalar(
            select(Setting.value).where(Setting.setting_key == "display_name")
        )
    return {
        "authenticated": authenticated,
        "display_name": display_name,
        # if .env is still the shipped example the interface should say so,
        # instead of letting the user bang on a password that cannot work
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
            f"Too many failed attempts. Try again in {wait} seconds.",
        )

    if not password_is_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No password configured: set APP_PASSWORD in the .env file and "
            "restart the container.",
        )

    if not check_password(password):
        record_failure(request)
        log.warning("Failed sign-in attempt from %s", request.client.host if request.client else "?")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong password")

    clear_failures(request)
    _set_cookie(response, make_token(get_secret(db)))
    return {"detail": "Signed in"}


@router.post("/auth/logout", response_model=Message)
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return Message(detail="Signed out")


@router.post("/auth/logout-everywhere", response_model=Message)
def logout_everywhere(response: Response, db: Session = Depends(get_db)):
    """Regenerates the signing secret: invalidates sessions on every device.
    Useful if you lose your phone."""
    reset_secret(db)
    response.delete_cookie(COOKIE_NAME, path="/")
    return Message(detail="All sessions have been closed")
