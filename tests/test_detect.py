"""Test dei rilevamenti automatici.

Ogni caso qui è nato da un errore vero visto su estratti conto reali, non da
un'ipotesi: il falso positivo di Steam, il buco di nove mesi di Discord, gli
acquisti singoli mescolati all'abbonamento.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models import Transaction
from app.services.detect import (
    _billing_day_is_stable,
    _cadence_for,
    _cluster_by_amount,
    _recent_run,
)


def tx(day: str, amount: str, description="X", account_id=1):
    t = Transaction()
    t.booked_at = date.fromisoformat(day)
    t.amount = Decimal(amount)
    t.description = description
    t.account_id = account_id
    return t


# ------------------------------------------------------------------ cadenze


@pytest.mark.parametrize(
    "gap, atteso",
    [(7, "weekly"), (30, "monthly"), (31, "monthly"), (28, "monthly"), (91, "quarterly"), (365, "yearly")],
)
def test_cadenze_riconosciute(gap, atteso):
    assert _cadence_for(gap)[0] == atteso


@pytest.mark.parametrize("gap", [3, 15, 50, 200, 500])
def test_cadenze_non_plausibili_scartate(gap):
    assert _cadence_for(gap) is None


# ------------------------------------------------- separazione per importo


def test_acquisti_singoli_separati_dall_abbonamento():
    """Caso Discord: 9,99 ogni mese (abbonamento) mescolato ad acquisti vari.

    Raggruppando tutto insieme la varianza degli importi fa scartare anche
    l'abbonamento vero.
    """
    items = [
        tx("2026-06-09", "-9.99"),
        tx("2026-07-09", "-9.99"),
        tx("2026-08-09", "-9.99"),
        tx("2026-06-11", "-2.99"),
        tx("2026-07-15", "-3.49"),
    ]
    clusters = _cluster_by_amount(items)
    grandi = [c for c in clusters if len(c) == 3]
    assert len(grandi) == 1
    assert all(abs(t.amount) == Decimal("9.99") for t in grandi[0])


def test_piccole_variazioni_restano_insieme():
    """Un aumento entro il 15% è lo stesso abbonamento, non uno nuovo."""
    items = [tx("2026-01-01", "-10.00"), tx("2026-02-01", "-10.50"), tx("2026-03-01", "-11.00")]
    assert len(_cluster_by_amount(items)) == 1


# ------------------------------------------------------- serie più recente


def test_buco_lungo_interrompe_la_serie():
    """Discord ha nove mesi di vuoto: prima e dopo sono due abbonamenti
    diversi, non uno solo con un buco."""
    items = [
        tx("2024-10-06", "-9.99"),
        tx("2025-07-09", "-9.99"),
        tx("2025-09-09", "-9.99"),
        tx("2025-10-09", "-9.99"),
    ]
    run = _recent_run(items, 30)
    assert len(run) == 3
    assert run[0].booked_at.isoformat() == "2025-07-09"


def test_un_mese_saltato_non_interrompe():
    """Un pagamento fallito capita: non deve far sparire l'abbonamento."""
    items = [
        tx("2026-01-09", "-9.99"),
        tx("2026-02-09", "-9.99"),
        tx("2026-04-09", "-9.99"),  # marzo saltato
        tx("2026-05-09", "-9.99"),
    ]
    assert len(_recent_run(items, 30)) == 4


# --------------------------------------------------- giorno di addebito


def test_abbonamento_stesso_giorno_del_mese():
    items = [tx("2026-06-14", "-20.99"), tx("2026-07-14", "-20.99"), tx("2026-08-14", "-20.99")]
    assert _billing_day_is_stable(items, "monthly")


def test_falso_positivo_steam_scartato():
    """Tre acquisti da ~40 € a tre mesi di distanza: importo simile e cadenza
    plausibile, ma i giorni sono 25, 24 e 14. Non è un abbonamento."""
    items = [tx("2025-11-25", "-41.84"), tx("2026-02-24", "-39.44"), tx("2026-05-14", "-38.98")]
    assert not _billing_day_is_stable(items, "quarterly")


def test_tolleranza_di_pochi_giorni():
    """Weekend e festivi spostano l'addebito di un paio di giorni."""
    items = [tx("2026-06-09", "-9.99"), tx("2026-07-11", "-9.99"), tx("2026-08-08", "-9.99")]
    assert _billing_day_is_stable(items, "monthly")


def test_giorni_a_cavallo_del_mese():
    """Il 31 e il 1 distano un giorno, non trenta."""
    items = [tx("2026-01-31", "-5.00"), tx("2026-03-01", "-5.00"), tx("2026-04-01", "-5.00")]
    assert _billing_day_is_stable(items, "monthly")


def test_giorni_sparsi_non_sono_abbonamento():
    items = [tx("2026-06-03", "-9.99"), tx("2026-07-19", "-9.99"), tx("2026-08-27", "-9.99")]
    assert not _billing_day_is_stable(items, "monthly")
