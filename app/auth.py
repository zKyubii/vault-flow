"""Autenticazione a password unica.

L'app è single-user e self-hosted: non serve un sistema di utenti, serve una
porta chiusa. La password sta nel `.env`, la sessione in un **cookie
httpOnly** firmato.

Perché il cookie httpOnly e non un token in localStorage:
- sopravvive alla chiusura dell'app sul telefono (una PWA che chiede la
  password ogni volta viene disinstallata dopo due giorni);
- non è leggibile dal JavaScript, quindi una singola falla XSS non regala la
  sessione a nessuno;
- viene inviato da solo con ogni `fetch`, senza codice che se ne ricordi.

Il segreto di firma **non** sta nel `.env`: viene generato al primo avvio e
salvato in `settings`. Così le sessioni sopravvivono ai riavvii senza
chiedere all'utente di configurare un'altra variabile, e cambiare la password
non invalida i cookie (sono cose diverse).
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

COOKIE_NAME = "spese_session"
SESSION_DAYS = 30
SECRET_KEY_SETTING = "session_secret"

# Password di esempio che non devono mai valere come credenziale: se il file
# `.env` è rimasto quello di partenza, l'app non deve essere aperta a chiunque
# conosca il repository.
PLACEHOLDER_PASSWORDS = {"", "cambiami", "cambiami-password-app", "changeme", "password"}

# Freno agli inserimenti a raffica. In memoria: l'app gira in un processo
# solo, e una tabella per questo sarebbe sovradimensionata.
#
# ⚠️ Dentro Docker (e ancor più dietro un reverse proxy sulla VPS) le
# richieste arrivano tutte dallo stesso indirizzo: il blocco è quindi di
# fatto **globale**, non per singolo visitatore. Su un'app a utente unico è
# accettabile — l'utente è uno solo — ma significa che un estraneo può
# chiudere fuori il proprietario a comando.
#
# Per questo la finestra è **corta**: 60 secondi rendono la forza bruta
# inutile (8 tentativi al minuto su una password decente non arrivano da
# nessuna parte) senza trasformare un dispetto in mezz'ora di blocco.
# Se un giorno l'app finisse dietro un proxy, il posto giusto per il limite
# è il proxy, che l'indirizzo vero ce l'ha.
_failures: dict[str, list[float]] = {}
MAX_ATTEMPTS = 8
LOCKOUT_WINDOW = 60  # secondi

_secret_cache: str | None = None


# --------------------------------------------------------------- segreto


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
    """Invalida tutte le sessioni attive (usato dal logout globale)."""
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
    # confronto a tempo costante: un `==` lascerebbe dedurre la firma un byte
    # alla volta misurando i tempi di risposta
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
    return request.client.host if request.client else "sconosciuto"


def rate_limited(request: Request) -> int:
    """Secondi di attesa residui, 0 se si può tentare."""
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


# ------------------------------------------------------------ dipendenza


def require_auth(
    db: Session = Depends(get_db),
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> None:
    """Protegge un router. Risponde 401 senza dettagli.

    Il messaggio non distingue "cookie assente" da "cookie scaduto" da "firma
    non valida": all'interfaccia serve solo sapere che deve mostrare il login,
    e a chi bussa non serve sapere altro.
    """
    if not verify_token(get_secret(db), session):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Authentication required",
            headers={"WWW-Authenticate": "Cookie"},
        )
