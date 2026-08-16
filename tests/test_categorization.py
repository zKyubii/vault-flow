"""Test del motore di categorizzazione.

Si testano le funzioni pure (matching, priorità, normalizzazione): non serve
un database. La protezione delle scelte manuali è verificata end-to-end
contro l'API, perché dipende dallo stato salvato.
"""

import pytest

from app.models import CategoryRule, Transaction
from app.services.categorization import (
    InvalidRule,
    match_rule,
    merchant_key,
    suggest_pattern,
    validate_pattern,
)


def rule(pattern, category_id=1, priority=100, match_type="contains", field="description", account_id=None):
    r = CategoryRule()
    r.id = priority  # basta un id stabile per l'ordinamento nei test
    r.pattern = pattern
    r.category_id = category_id
    r.priority = priority
    r.match_type = match_type
    r.field = field
    r.account_id = account_id
    r.enabled = True
    return r


def transaction(description="", counterparty=None, account_id=1):
    t = Transaction()
    t.description = description
    t.counterparty = counterparty
    t.account_id = account_id
    return t


# ------------------------------------------------------------- match types


@pytest.mark.parametrize(
    "match_type, pattern, description, atteso",
    [
        ("contains", "steam", "Acquisto su Steam Store", True),
        ("contains", "steam", "Spotify", False),
        ("starts_with", "amazon", "Amazon Prime", True),
        ("starts_with", "prime", "Amazon Prime", False),
        ("exact", "steam", "Steam", True),
        ("exact", "steam", "Steam Store", False),
        ("regex", r"^riot\s+games$", "Riot Games", True),
        ("regex", r"^riot\s+games$", "Riot Games Store", False),
    ],
)
def test_match_types(match_type, pattern, description, atteso):
    r = rule(pattern, match_type=match_type)
    assert (match_rule(transaction(description), [r]) is not None) is atteso


def test_matching_insensibile_alle_maiuscole():
    assert match_rule(transaction("SPOTIFY AB"), [rule("spotify")]) is not None
    assert match_rule(transaction("spotify ab"), [rule("SPOTIFY")]) is not None


def test_descrizione_vuota_non_combacia():
    assert match_rule(transaction(""), [rule("steam")]) is None


# ---------------------------------------------------------------- priorità


def test_priorita_piu_bassa_vince():
    """'amazon prime' deve battere 'amazon', altrimenti gli abbonamenti
    finirebbero fra gli acquisti."""
    regole = sorted(
        [rule("amazon", category_id=10, priority=100), rule("amazon prime", category_id=20, priority=10)],
        key=lambda r: (r.priority, r.id),
    )
    vincente = match_rule(transaction("Amazon Prime"), regole)
    assert vincente.category_id == 20


def test_ordine_deterministico_a_parita_di_priorita():
    regole = sorted(
        [rule("apple", category_id=10, priority=100), rule("app", category_id=20, priority=100)],
        key=lambda r: (r.priority, r.id),
    )
    # stessa priorità: decide l'id, in modo stabile fra esecuzioni
    assert match_rule(transaction("Apple"), regole) is not None


# ------------------------------------------------------------------ campi


def test_regola_su_controparte():
    r = rule("mario rossi", field="counterparty")
    assert match_rule(transaction("Bonifico", counterparty="Mario Rossi"), [r]) is not None
    # lo stesso testo nella descrizione non deve attivarla
    assert match_rule(transaction("Mario Rossi"), [r]) is None


def test_regola_limitata_a_un_conto():
    r = rule("steam", account_id=2)
    assert match_rule(transaction("Steam", account_id=2), [r]) is not None
    assert match_rule(transaction("Steam", account_id=1), [r]) is None


def test_regola_globale_vale_per_tutti_i_conti():
    r = rule("steam", account_id=None)
    assert match_rule(transaction("Steam", account_id=99), [r]) is not None


def test_regex_invalida_non_fa_esplodere_tutto():
    """Una regola diventata invalida viene ignorata, non blocca le altre."""
    rotta = rule("([unclosed", match_type="regex", priority=1)
    buona = rule("steam", priority=2)
    assert match_rule(transaction("Steam"), [rotta, buona]).pattern == "steam"


# ------------------------------------------------------------- validazione


def test_validate_pattern_rifiuta_vuoto():
    with pytest.raises(InvalidRule):
        validate_pattern("contains", "   ")


def test_validate_pattern_rifiuta_regex_invalida():
    with pytest.raises(InvalidRule):
        validate_pattern("regex", "([unclosed")


def test_validate_pattern_accetta_regex_valida():
    validate_pattern("regex", r"^amazon\s")


# ------------------------------------------------------------ suggerimenti


@pytest.mark.parametrize(
    "descrizione, atteso",
    [
        ("Top-up by *1234", "top-up by"),
        ("Top-up by *5678", "top-up by"),   # stessa chiave: una regola sola
        ("Steam", "steam"),
        ("Incoming transfer from MARIO ROSSI (IT60X0542811101000000123456)", "incoming transfer from mario rossi"),
        ("Bottega 4821", "bottega"),
    ],
)
def test_merchant_key(descrizione, atteso):
    assert merchant_key(descrizione) == atteso


def test_merchant_key_su_descrizione_vuota():
    assert merchant_key(None) == ""
    assert merchant_key("   ") == ""


# ------------------------------------------------------- pattern cercabili


@pytest.mark.parametrize(
    "descrizione, atteso",
    [
        ("Steam", "steam"),
        ("Bottega 4821", "bottega"),
        ("Top-up by *1234", "top-up by"),
        # merchant_key darebbe "g a com", che non è contenuto nella descrizione
        ("G2a Com", "g2a com"),
        ("Incoming transfer from MARIO ROSSI (IT60X0542811101000000123456)",
         "incoming transfer from mario rossi"),
        ("The Space Cinema", "the space cinema"),
    ],
)
def test_suggest_pattern(descrizione, atteso):
    assert suggest_pattern(descrizione) == atteso


@pytest.mark.parametrize(
    "descrizione",
    ["Steam", "Bottega 4821", "G2a Com", "Top-up by *1234", "The Space Cinema", "A.b.c."],
)
def test_pattern_e_sempre_contenuto_nella_descrizione(descrizione):
    """La proprietà che conta: una regola 'contiene' costruita su questo
    pattern deve trovare la transazione da cui è nata."""
    assert suggest_pattern(descrizione) in descrizione.lower()


def test_pattern_non_si_svuota_su_descrizioni_tutte_numeriche():
    """Se togliendo i numeri non resta nulla, meglio la descrizione intera che
    un pattern vuoto che prenderebbe tutto."""
    assert suggest_pattern("12345") == "12345"
