"""Tests for authentication.

These cover the token logic and the attempt brake: they are pure functions,
and they are the part where a mistake is invisible — a broken token "works"
fine until somebody tries to forge one.

No real password appears here.
"""

import time
from types import SimpleNamespace

import pytest

from app import auth


@pytest.fixture(autouse=True)
def clean_brake():
    auth._failures.clear()
    yield
    auth._failures.clear()


def request(host="10.0.0.1"):
    return SimpleNamespace(client=SimpleNamespace(host=host))


SECRET = "a-test-secret-long-enough-to-be-realistic"


# ------------------------------------------------------------------ token


def test_a_valid_token_is_accepted():
    assert auth.verify_token(SECRET, auth.make_token(SECRET))


def test_a_token_from_another_secret_is_rejected():
    """If the secret changes (global sign-out) old sessions fall."""
    token = auth.make_token(SECRET)
    assert not auth.verify_token("a-completely-different-secret", token)


def test_a_tampered_signature_is_rejected():
    token = auth.make_token(SECRET)
    payload, signature = token.split(".", 1)
    tampered = f"{payload}.{'A' * len(signature)}"
    assert not auth.verify_token(SECRET, tampered)


def test_tampered_content_is_rejected():
    """Extending the expiry by editing the payload must break the signature."""
    token = auth.make_token(SECRET)
    _, signature = token.split(".", 1)
    fake_payload = auth._b64encode(b'{"exp": 99999999999}')
    assert not auth.verify_token(SECRET, f"{fake_payload}.{signature}")


def test_an_expired_token_is_rejected():
    expired = auth.make_token(SECRET, days=-1)
    assert not auth.verify_token(SECRET, expired)


@pytest.mark.parametrize("value", [None, "", "no-dot", ".", "a.b", "...."])
def test_malformed_tokens_do_not_blow_up(value):
    assert auth.verify_token(SECRET, value) is False


def test_the_token_does_not_carry_the_password():
    """The cookie travels with every request: it holds nothing but an expiry."""
    token = auth.make_token(SECRET)
    payload = auth._b64decode(token.split(".", 1)[0]).decode()
    assert "exp" in payload
    assert SECRET not in token


def test_expiry_is_thirty_days_out():
    token = auth.make_token(SECRET)
    import json

    data = json.loads(auth._b64decode(token.split(".", 1)[0]))
    expected = time.time() + auth.SESSION_DAYS * 86400
    assert abs(data["exp"] - expected) < 60


# -------------------------------------------------------------- password


@pytest.mark.parametrize("value", ["", "cambiami", "changeme", "password", "CAMBIAMI"])
def test_example_passwords_never_count_as_a_credential(value, monkeypatch):
    """If .env was left as shipped, the app must not be open to anyone who
    read the repository."""
    monkeypatch.setattr(
        auth, "get_settings", lambda: SimpleNamespace(app_password=value)
    )
    assert not auth.password_is_configured()
    assert not auth.check_password(value)


def test_a_configured_password_is_recognised(monkeypatch):
    monkeypatch.setattr(
        auth, "get_settings", lambda: SimpleNamespace(app_password="a-real-password-123")
    )
    assert auth.password_is_configured()
    assert auth.check_password("a-real-password-123")
    assert not auth.check_password("a-real-password-124")


# ----------------------------------------------------------------- brake


def test_the_brake_kicks_in_past_the_limit():
    req = request()
    for _ in range(auth.MAX_ATTEMPTS):
        assert auth.rate_limited(req) == 0
        auth.record_failure(req)
    assert auth.rate_limited(req) > 0


def test_a_successful_sign_in_clears_the_count():
    req = request()
    for _ in range(auth.MAX_ATTEMPTS):
        auth.record_failure(req)
    auth.clear_failures(req)
    assert auth.rate_limited(req) == 0


def test_old_attempts_expire():
    req = request()
    old = time.time() - auth.LOCKOUT_WINDOW - 1
    auth._failures[req.client.host] = [old] * auth.MAX_ATTEMPTS
    assert auth.rate_limited(req) == 0


def test_the_lockout_lasts_under_two_minutes():
    """Behind Docker the lockout is effectively global: if it lasted long, a
    stranger could lock the owner out on demand."""
    assert auth.LOCKOUT_WINDOW <= 120
