"""Test dell'autenticazione.

Si testa la logica dei token e del freno ai tentativi: sono funzioni pure e
sono la parte in cui un errore non si vede a occhio, perché un token rotto
"funziona" comunque finché qualcuno non prova a falsificarlo.

Nessuna password reale entra qui.
"""

import time
from types import SimpleNamespace

import pytest

from app import auth


@pytest.fixture(autouse=True)
def freno_pulito():
    auth._failures.clear()
    yield
    auth._failures.clear()


def richiesta(host="10.0.0.1"):
    return SimpleNamespace(client=SimpleNamespace(host=host))


SEGRETO = "segreto-di-prova-abbastanza-lungo-da-essere-realistico"


# ------------------------------------------------------------------ token


def test_token_valido_viene_accettato():
    assert auth.verify_token(SEGRETO, auth.make_token(SEGRETO))


def test_token_di_un_altro_segreto_viene_respinto():
    """Se il segreto cambia (logout globale) le sessioni vecchie cadono."""
    token = auth.make_token(SEGRETO)
    assert not auth.verify_token("un-altro-segreto-diverso", token)


def test_firma_alterata_viene_respinta():
    token = auth.make_token(SEGRETO)
    payload, signature = token.split(".", 1)
    alterato = f"{payload}.{'A' * len(signature)}"
    assert not auth.verify_token(SEGRETO, alterato)


def test_contenuto_alterato_viene_respinto():
    """Allungare la scadenza modificando il payload deve invalidare la firma."""
    token = auth.make_token(SEGRETO)
    _, signature = token.split(".", 1)
    falso_payload = auth._b64encode(b'{"exp": 99999999999}')
    assert not auth.verify_token(SEGRETO, f"{falso_payload}.{signature}")


def test_token_scaduto_viene_respinto():
    scaduto = auth.make_token(SEGRETO, days=-1)
    assert not auth.verify_token(SEGRETO, scaduto)


@pytest.mark.parametrize("valore", [None, "", "senza-punto", ".", "a.b", "...."])
def test_token_malformati_non_fanno_esplodere_nulla(valore):
    assert auth.verify_token(SEGRETO, valore) is False


def test_il_token_non_contiene_la_password():
    """Il cookie viaggia a ogni richiesta: dentro ci sta solo una scadenza."""
    token = auth.make_token(SEGRETO)
    payload = auth._b64decode(token.split(".", 1)[0]).decode()
    assert "exp" in payload
    assert SEGRETO not in token


def test_scadenza_a_trenta_giorni():
    token = auth.make_token(SEGRETO)
    import json

    data = json.loads(auth._b64decode(token.split(".", 1)[0]))
    attesa = time.time() + auth.SESSION_DAYS * 86400
    assert abs(data["exp"] - attesa) < 60


# -------------------------------------------------------------- password


@pytest.mark.parametrize("valore", ["", "cambiami", "changeme", "password", "CAMBIAMI"])
def test_password_di_esempio_non_valgono_come_credenziale(valore, monkeypatch):
    """Se il .env è rimasto quello di partenza l'app non deve essere aperta a
    chiunque conosca il repository."""
    monkeypatch.setattr(
        auth, "get_settings", lambda: SimpleNamespace(app_password=valore)
    )
    assert not auth.password_is_configured()
    assert not auth.check_password(valore)


def test_password_impostata_viene_riconosciuta(monkeypatch):
    monkeypatch.setattr(
        auth, "get_settings", lambda: SimpleNamespace(app_password="una-password-vera-123")
    )
    assert auth.password_is_configured()
    assert auth.check_password("una-password-vera-123")
    assert not auth.check_password("una-password-vera-124")


# ----------------------------------------------------------------- freno


def test_il_freno_scatta_dopo_il_limite():
    req = richiesta()
    for _ in range(auth.MAX_ATTEMPTS):
        assert auth.rate_limited(req) == 0
        auth.record_failure(req)
    assert auth.rate_limited(req) > 0


def test_un_accesso_riuscito_azzera_il_conto():
    req = richiesta()
    for _ in range(auth.MAX_ATTEMPTS):
        auth.record_failure(req)
    auth.clear_failures(req)
    assert auth.rate_limited(req) == 0


def test_i_tentativi_vecchi_scadono():
    req = richiesta()
    vecchio = time.time() - auth.LOCKOUT_WINDOW - 1
    auth._failures[req.client.host] = [vecchio] * auth.MAX_ATTEMPTS
    assert auth.rate_limited(req) == 0


def test_il_blocco_dura_meno_di_due_minuti():
    """Dietro Docker il blocco è di fatto globale: se durasse a lungo, un
    estraneo potrebbe chiudere fuori il proprietario a comando."""
    assert auth.LOCKOUT_WINDOW <= 120
