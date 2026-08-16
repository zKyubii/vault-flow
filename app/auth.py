"""Single-password authentication.

The app is single-user and self-hosted: it does not need a user system, it
needs a locked door. The password lives in `.env`, the session in a signed
**httpOnly cookie**.

Why an httpOnly cookie and not a token in localStorage:
- it survives closing the app on a phone (a PWA that asks for the password
  every time gets uninstalled within two days);
- it is not readable from JavaScript, so a single XSS hole does not hand the
  session to anyone;
- it is sent automatically with every `fetch`, with no code to remember it.

The signing secret is **not** in `.env`: it is generated on first boot and
stored in `settings`. That way sessions survive restarts without asking the
user to configure another variable, and changing the password does not
invalidate cookies (they are different things).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Setting

COOKIE_NAME = "vaultflow_session"
SESSION_DAYS = 30
SECRET_KEY_SETTING = "session_secret"

# Example passwords that must never count as a credential: if the `.env` file
# was left as shipped, the app must not be open to anyone who read the
# repository.
PLACEHOLDER_PASSWORDS = {"", "cambiami", "cambiami-password-app", "changeme", "password"}

# Brake on rapid-fire attempts. Kept in memory: the app runs as a single
# process, and a table for this would be overkill.
#
# ⚠️ Inside Docker (and even more behind a reverse proxy on a VPS) every
# request arrives from the same address, so the lockout is effectively
# **global**, not per visitor. On a single-user app that is acceptable — there
# is only one user — but it means a stranger can lock the owner out on demand.
#
# That is why the window is **short**: 60 seconds makes brute force useless
# (8 attempts a minute against a decent password gets nowhere) without turning
# a prank into half an hour of lockout. If the app ever sits behind a proxy,
# the right place for the limit is the proxy, which knows the real address.
_failures: dict[str, list[float]] = {}
MAX_ATTEMPTS = 8
LOCKOUT_WINDOW = 60  # seconds

_secret_cache: str | None = None


# ---------------------------------------------------------------- secret


def get_secret(db: Session) -> str:
    global _secret_cache
    if _secret_cache:
        return _secret_cache

    row = db.get(Setting, SECRET_KEY_SETTING)
    if row and row.value:
        _secret_cache = row.value
        return _secret_cache

    value = secrets.token_urlsafe(48)
    db.merge(Setting(setting_key=SECRET_KEY_SETTING, value=value))
    db.commit()
    _secret_cache = value
    return value


def reset_secret(db: Session) -> str:
    """Invalidate every active session (used by the global sign-out)."""
    global _secret_cache
    value = secrets.token_urlsafe(48)
    db.merge(Setting(setting_key=SECRET_KEY_SETTING, value=value))
    db.commit()
    _secret_cache = value
    return value


# ----------------------------------------------------------------- token


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(secret: str, days: int = SESSION_DAYS) -> str:
    payload = _b64encode(json.dumps({"exp": int(time.time()) + days * 86400}).encode())
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64encode(signature)}"


def verify_token(secret: str, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    payload, signature = token.split(".", 1)

    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    try:
        given = _b64decode(signature)
    except Exception:
        return False
    # constant-time comparison: a plain `==` would let someone recover the
    # signature one byte at a time by measuring response times
    if not hmac.compare_digest(expected, given):
        return False

    try:
        data = json.loads(_b64decode(payload))
    except Exception:
        return False
    return int(data.get("exp", 0)) > time.time()


# -------------------------------------------------------------- password


def password_is_configured() -> bool:
    return get_settings().app_password.strip().lower() not in PLACEHOLDER_PASSWORDS


def check_password(candidate: str) -> bool:
    if not password_is_configured():
        return False
    return hmac.compare_digest(candidate, get_settings().app_password)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limited(request: Request) -> int:
    """Seconds left to wait, 0 if an attempt is allowed."""
    key = _client_key(request)
    now = time.time()
    attempts = [t for t in _failures.get(key, []) if now - t < LOCKOUT_WINDOW]
    _failures[key] = attempts
    if len(attempts) < MAX_ATTEMPTS:
        return 0
    return int(LOCKOUT_WINDOW - (now - attempts[0])) + 1


def record_failure(request: Request) -> None:
    _failures.setdefault(_client_key(request), []).append(time.time())


def clear_failures(request: Request) -> None:
    _failures.pop(_client_key(request), None)


# ------------------------------------------------------------ dependency


def require_auth(
    db: Session = Depends(get_db),
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> None:
    """Protects a router. Answers 401 with no detail.

    The message does not distinguish "no cookie" from "expired cookie" from
    "bad signature": the interface only needs to know it should show the login,
    and whoever is knocking needs to know nothing else.
    """
    if not verify_token(get_secret(db), session):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Authentication required",
            headers={"WWW-Authenticate": "Cookie"},
        )
