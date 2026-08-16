"""Automatic detection: recurring subscriptions and transfers between your
own accounts.

Both try to infer something the data does not state. So: **they propose, they
do not decide.** The user sees what was found and confirms. An app that
re-categorises on its own is one you stop trusting the moment the numbers look
odd.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, Category, Transaction
from app.services.categorization import merchant_key

# --------------------------------------------------------------------------
# recurring subscriptions
# --------------------------------------------------------------------------

# Cadence bands, with tolerance: banks post the charge a few days off and
# months are not all the same length.
CADENCES = [
    ("weekly", 6, 8, Decimal("4.333")),
    ("monthly", 25, 36, Decimal(1)),
    ("quarterly", 85, 96, Decimal("0.333")),
    ("yearly", 350, 380, Decimal("0.0833")),
]

# How much the amount may drift and still be "the same subscription".
AMOUNT_TOLERANCE = Decimal("0.15")


def _cadence_for(gap: float) -> tuple[str, Decimal] | None:
    for name, low, high, factor in CADENCES:
        if low <= gap <= high:
            return name, factor
    return None


def _cluster_by_amount(items: list[Transaction]) -> list[list[Transaction]]:
    """Within the same merchant, different amounts are different things.

    A chat app might charge 9.99 on the 9th of every month (a subscription)
    mixed with 10.13, 3.49 and 2.99 one-off purchases. Grouped together, the
    variance throws away the real subscription as well. Split by amount, it
    survives.

    A price increase splits the group in two: both halves are still evaluated,
    so at worst a little history is lost.
    """
    clusters: list[dict] = []
    for transaction in sorted(items, key=lambda t: abs(t.amount)):
        amount = abs(transaction.amount)
        for cluster in clusters:
            reference = cluster["reference"]
            if reference > 0 and abs(amount - reference) / reference <= AMOUNT_TOLERANCE:
                cluster["items"].append(transaction)
                break
        else:
            clusters.append({"reference": amount, "items": [transaction]})
    return [cluster["items"] for cluster in clusters]


def _recent_run(items: list[Transaction], typical_gap: float) -> list[Transaction]:
    """The most recent regular sequence, walking back from the last charge.

    A gap is accepted if it is roughly 1, 2 or 3 times the cadence: a skipped
    month does not break the series, while a nine-month hole does — because
    that is a different subscription, cancelled and started again.
    """
    if typical_gap <= 0:
        return items

    run = [items[-1]]
    for index in range(len(items) - 1, 0, -1):
        gap = (items[index].booked_at - items[index - 1].booked_at).days
        multiple = round(gap / typical_gap)
        if 1 <= multiple <= 3 and abs(gap - multiple * typical_gap) <= max(6, typical_gap * 0.35):
            run.append(items[index - 1])
        else:
            break
    run.reverse()
    return run


def _billing_day_is_stable(items: list[Transaction], cadence: str) -> bool:
    """A subscription always bills on the same day.

    This is the discriminator that separates a subscription from a
    coincidence. Without it, three game purchases of about €40 that happened
    to fall three months apart get mistaken for a quarterly subscription:
    similar amount, plausible cadence, but the days were 25, 24 and 14. Real
    subscriptions land on the same day every time.
    """
    if cadence == "weekly":
        days = [t.booked_at.weekday() for t in items]
        reference = median(days)
        return all(min(abs(d - reference), 7 - abs(d - reference)) <= 1 for d in days)

    days = [t.booked_at.day for t in items]
    reference = median(days)
    # three days of tolerance: weekends and holidays shift the charge
    return all(min(abs(d - reference), 31 - abs(d - reference)) <= 3 for d in days)


def detect_subscriptions(
    db: Session,
    *,
    min_occurrences: int = 3,
    months_back: int = 18,
) -> dict:
    """Finds charges that repeat at a regular interval with a stable amount.

    **Both** conditions are required. Regularity alone is not enough: a big
    retailer shows up every month with amounts from 6 to 1,157, and that is
    not a subscription.
    """
    cutoff = date.today() - timedelta(days=months_back * 31)

    by_merchant: dict[str, list[Transaction]] = defaultdict(list)
    for transaction in db.scalars(
        select(Transaction).where(Transaction.amount < 0, Transaction.booked_at >= cutoff)
    ):
        key = merchant_key(transaction.description)
        if key:
            by_merchant[key].append(transaction)

    groups: list[tuple[str, list[Transaction]]] = []
    for key, transactions in by_merchant.items():
        for cluster in _cluster_by_amount(transactions):
            groups.append((key, cluster))

    names = {c.id: c.name for c in db.scalars(select(Category))}
    found: list[dict] = []

    for key, items in groups:
        if len(items) < min_occurrences:
            continue

        items.sort(key=lambda t: t.booked_at)
        gaps = [
            (b.booked_at - a.booked_at).days
            for a, b in zip(items, items[1:])
            if (b.booked_at - a.booked_at).days > 0
        ]
        if len(gaps) < min_occurrences - 1:
            continue

        typical_gap = median(gaps)
        cadence = _cadence_for(typical_gap)
        if cadence is None:
            continue

        # Only the most recent regular sequence is considered, not the whole
        # history. A real subscription has holes: failed payments, paused
        # months, cancellations and restarts. Requiring *every* interval to be
        # regular throws away the most obvious subscriptions.
        items = _recent_run(items, typical_gap)
        if len(items) < min_occurrences:
            continue

        if not _billing_day_is_stable(items, cadence[0]):
            continue

        amounts = [abs(t.amount) for t in items]
        typical_amount = Decimal(median(amounts))
        if typical_amount == 0:
            continue
        stable = sum(
            1 for a in amounts if abs(a - typical_amount) / typical_amount <= AMOUNT_TOLERANCE
        )
        if stable / len(amounts) < 0.7:
            continue

        name, factor = cadence
        last = items[-1].booked_at
        next_expected = last + timedelta(days=int(typical_gap))
        # active if the next charge is not already badly overdue
        active = (date.today() - last).days <= typical_gap * 1.6

        found.append(
            {
                "pattern": key,
                "description": items[-1].description,
                "occurrences": len(items),
                "cadence": name,
                "typical_gap_days": int(typical_gap),
                "amount": typical_amount,
                "monthly_cost": (typical_amount * factor).quantize(Decimal("0.01")),
                "first_seen": items[0].booked_at,
                "last_seen": last,
                "next_expected": next_expected,
                "active": active,
                "category": names.get(items[-1].category_id),
                "total_spent": sum(amounts),
                "account_id": items[-1].account_id,
            }
        )

    found.sort(key=lambda s: (not s["active"], -s["monthly_cost"]))
    monthly_total = sum((s["monthly_cost"] for s in found if s["active"]), Decimal(0))

    return {
        "monthly_total": monthly_total,
        "yearly_total": (monthly_total * 12).quantize(Decimal("0.01")),
        "active_count": sum(1 for s in found if s["active"]),
        "subscriptions": found,
    }


# --------------------------------------------------------------------------
# transfers between your own accounts
# --------------------------------------------------------------------------


def detect_transfers(db: Session, *, window_days: int = 5) -> list[dict]:
    """Looks for pairs of equal and opposite amounts on two different accounts.

    That is the same money moving: counting it as both spending and income
    inflates the totals twice, often by thousands a year.

    Matching is **one to one**: once paired, a transaction cannot be reused.
    Without that, three charges of 3.50 would produce nine pairs instead of
    one.
    """
    accounts = {a.id: a.name for a in db.scalars(select(Account))}
    excluded = {
        c.id
        for c in db.scalars(select(Category).where(Category.exclude_from_stats == True))  # noqa: E712
    }

    transactions = list(db.scalars(select(Transaction).order_by(Transaction.booked_at)))
    outgoing = [t for t in transactions if t.amount < 0]
    incoming_by_amount: dict[Decimal, list[Transaction]] = defaultdict(list)
    for t in transactions:
        if t.amount > 0:
            incoming_by_amount[t.amount].append(t)

    used: set[int] = set()
    pairs: list[dict] = []

    for out in outgoing:
        if out.id in used:
            continue
        candidates = incoming_by_amount.get(-out.amount, [])
        for inc in candidates:
            if inc.id in used or inc.account_id == out.account_id:
                continue
            if abs((inc.booked_at - out.booked_at).days) > window_days:
                continue

            used.add(out.id)
            used.add(inc.id)
            pairs.append(
                {
                    "amount": abs(out.amount),
                    "days_apart": abs((inc.booked_at - out.booked_at).days),
                    "already_marked": out.category_id in excluded and inc.category_id in excluded,
                    "out": {
                        "id": out.id,
                        "account": accounts.get(out.account_id, "?"),
                        "booked_at": out.booked_at,
                        "description": out.description,
                        "category_id": out.category_id,
                    },
                    "in": {
                        "id": inc.id,
                        "account": accounts.get(inc.account_id, "?"),
                        "booked_at": inc.booked_at,
                        "description": inc.description,
                        "category_id": inc.category_id,
                    },
                }
            )
            break

    pairs.sort(key=lambda p: -p["amount"])
    return pairs


def apply_transfers(
    db: Session, *, category_id: int, window_days: int = 5, pair_ids: list[int] | None = None
) -> dict:
    """Assigns the transfer category to the detected pairs.

    Transactions categorised by hand are not touched: that holds here as
    everywhere else in the project.
    """
    pairs = detect_transfers(db, window_days=window_days)
    wanted = set(pair_ids or [])

    updated = 0
    skipped_manual = 0
    for pair in pairs:
        for side in ("out", "in"):
            entry = pair[side]
            if wanted and entry["id"] not in wanted:
                continue
            transaction = db.get(Transaction, entry["id"])
            if transaction is None:
                continue
            if transaction.category_source == "manual":
                skipped_manual += 1
                continue
            if transaction.category_id == category_id:
                continue
            transaction.category_id = category_id
            transaction.category_source = "rule"
            updated += 1

    db.commit()
    return {"pairs": len(pairs), "updated": updated, "skipped_manual": skipped_manual}
