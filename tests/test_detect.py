"""Tests for the automatic detection.

Every case here came from a real mistake seen against real bank statements,
not from a hypothesis: the false-positive subscription, the nine-month gap,
one-off purchases mixed in with a real subscription.
"""

from datetime import date
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


# ------------------------------------------------------------------ cadence


@pytest.mark.parametrize(
    "gap, expected",
    [(7, "weekly"), (30, "monthly"), (31, "monthly"), (28, "monthly"), (91, "quarterly"), (365, "yearly")],
)
def test_known_cadences_are_recognised(gap, expected):
    assert _cadence_for(gap)[0] == expected


@pytest.mark.parametrize("gap", [3, 15, 50, 200, 500])
def test_implausible_cadences_are_rejected(gap):
    assert _cadence_for(gap) is None


# ------------------------------------------------------- splitting by amount


def test_one_off_purchases_are_split_from_the_subscription():
    """Real case: 9.99 every month (a subscription) mixed with odd purchases.

    Grouped together, the variance in the amounts throws away the real
    subscription as well.
    """
    items = [
        tx("2026-06-09", "-9.99"),
        tx("2026-07-09", "-9.99"),
        tx("2026-08-09", "-9.99"),
        tx("2026-06-11", "-2.99"),
        tx("2026-07-15", "-3.49"),
    ]
    clusters = _cluster_by_amount(items)
    big = [c for c in clusters if len(c) == 3]
    assert len(big) == 1
    assert all(abs(t.amount) == Decimal("9.99") for t in big[0])


def test_small_price_changes_stay_together():
    """An increase within 15% is the same subscription, not a new one."""
    items = [tx("2026-01-01", "-10.00"), tx("2026-02-01", "-10.50"), tx("2026-03-01", "-11.00")]
    assert len(_cluster_by_amount(items)) == 1


# --------------------------------------------------------- most recent run


def test_a_long_gap_breaks_the_series():
    """Nine months of silence: before and after are two different
    subscriptions, not one with a hole in it."""
    items = [
        tx("2024-10-06", "-9.99"),
        tx("2025-07-09", "-9.99"),
        tx("2025-09-09", "-9.99"),
        tx("2025-10-09", "-9.99"),
    ]
    run = _recent_run(items, 30)
    assert len(run) == 3
    assert run[0].booked_at.isoformat() == "2025-07-09"


def test_one_skipped_month_does_not_break_the_series():
    """A failed payment happens: it must not make the subscription vanish."""
    items = [
        tx("2026-01-09", "-9.99"),
        tx("2026-02-09", "-9.99"),
        tx("2026-04-09", "-9.99"),  # March skipped
        tx("2026-05-09", "-9.99"),
    ]
    assert len(_recent_run(items, 30)) == 4


# ------------------------------------------------------------- billing day


def test_subscription_bills_on_the_same_day_each_month():
    items = [tx("2026-06-14", "-20.99"), tx("2026-07-14", "-20.99"), tx("2026-08-14", "-20.99")]
    assert _billing_day_is_stable(items, "monthly")


def test_false_positive_is_rejected_by_the_billing_day():
    """Three purchases of about 40 three months apart: similar amount and a
    plausible cadence, but the days are 25, 24 and 14. Not a subscription."""
    items = [tx("2025-11-25", "-41.84"), tx("2026-02-24", "-39.44"), tx("2026-05-14", "-38.98")]
    assert not _billing_day_is_stable(items, "quarterly")


def test_a_few_days_of_drift_are_tolerated():
    """Weekends and public holidays shift the charge by a day or two."""
    items = [tx("2026-06-09", "-9.99"), tx("2026-07-11", "-9.99"), tx("2026-08-08", "-9.99")]
    assert _billing_day_is_stable(items, "monthly")


def test_days_across_the_month_boundary():
    """The 31st and the 1st are one day apart, not thirty."""
    items = [tx("2026-01-31", "-5.00"), tx("2026-03-01", "-5.00"), tx("2026-04-01", "-5.00")]
    assert _billing_day_is_stable(items, "monthly")


def test_scattered_days_are_not_a_subscription():
    items = [tx("2026-06-03", "-9.99"), tx("2026-07-19", "-9.99"), tx("2026-08-27", "-9.99")]
    assert not _billing_day_is_stable(items, "monthly")
