"""Aggregations for the dashboard.

The sums are computed in the database, not in the browser: at 400 rows the
difference is invisible, at 20,000 it is not. And a phone should not download
the whole history just to show a total.

Categories flagged `exclude_from_stats` (transfers between your own accounts,
broker deposits, opening balances) stay in the balances but do **not** count
as income or spending: that money was moved, not earned or spent.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.filters import TxFilters, apply_exclusion, apply_filters, excluded_category_ids
from app.models import Account, Category, Transaction

# Income and expenses are computed **per transaction**, not by summing per
# category first: a refund inside "Shopping" would cancel part of the
# purchases and disappear from both totals. People want to know how much went
# out and how much came in, not the net balance of each category.
_POSITIVE = func.coalesce(func.sum(case((Transaction.amount > 0, Transaction.amount), else_=0)), 0)
_NEGATIVE = func.coalesce(func.sum(case((Transaction.amount < 0, Transaction.amount), else_=0)), 0)


def _totals(db: Session, filters: TxFilters, excluded: set[int]) -> tuple[Decimal, Decimal]:
    query = apply_exclusion(apply_filters(select(_POSITIVE, _NEGATIVE), filters), filters, excluded)
    income, expense = db.execute(query).one()
    return Decimal(income or 0), Decimal(expense or 0)


def summary(db: Session, filters: TxFilters) -> dict:
    excluded = excluded_category_ids(db)
    names = {c.id: c for c in db.scalars(select(Category))}

    income, expense = _totals(db, filters, excluded)

    # Money moved between your own accounts: only the outgoing side is
    # counted, otherwise every transfer would be counted twice.
    moved = Decimal(0)
    if excluded and not filters.category_ids:
        moved = abs(
            Decimal(
                db.execute(
                    apply_filters(
                        select(_NEGATIVE).where(Transaction.category_id.in_(excluded)), filters
                    )
                ).scalar()
                or 0
            )
        )

    grouped = db.execute(
        apply_exclusion(
            apply_filters(
                select(
                    Transaction.category_id,
                    func.sum(Transaction.amount),
                    func.count(Transaction.id),
                ),
                filters,
            ),
            filters,
            excluded,
        ).group_by(Transaction.category_id)
    ).all()

    by_category = [
        {
            "category_id": category_id,
            "name": names[category_id].name if category_id in names else "Uncategorised",
            "color": names[category_id].color if category_id in names else "#9e9e9e",
            "is_income": bool(names[category_id].is_income) if category_id in names else False,
            "total": Decimal(total or 0),
            "count": count,
        }
        for category_id, total, count in grouped
    ]
    by_category.sort(key=lambda c: c["total"])

    return {
        "date_from": filters.date_from,
        "date_to": filters.date_to,
        "income": income,
        "expense": expense,
        "net": income + expense,
        "transferred": moved,
        "by_category": by_category,
    }


def _last_day_of_month(day: date) -> date:
    following = day.replace(day=28) + timedelta(days=4)
    return following - timedelta(days=following.day)


def _previous_range(start: date, end: date, span: int) -> tuple[date, date]:
    """The period to compare against.

    If the range is **exactly one calendar month**, the previous period is the
    calendar month before it — not "the N days before". Comparing July
    (31 days) with "June plus the 31st of May" gives a number that is right in
    the abstract and wrong in the reader's head.
    """
    is_full_month = start.day == 1 and end == _last_day_of_month(start) and start.month == end.month
    if is_full_month:
        previous_end = start - timedelta(days=1)
        return previous_end.replace(day=1), previous_end
    return start - timedelta(days=span), start - timedelta(days=1)


def compare_previous(db: Session, filters: TxFilters) -> dict | None:
    """Compares the period with the immediately preceding one of equal length.

    Without something to compare against, "you spent 800" says nothing: the
    point is knowing whether that is a lot *for you*.
    """
    if filters.date_from is None:
        return None

    end = filters.date_to or date.today()
    span = (end - filters.date_from).days + 1
    if span <= 0:
        return None

    previous_from, previous_to = _previous_range(filters.date_from, end, span)

    previous = TxFilters(
        date_from=previous_from,
        date_to=previous_to,
        account_ids=filters.account_ids,
        category_ids=filters.category_ids,
        kind=filters.kind,
        search=filters.search,
        uncategorized=filters.uncategorized,
    )
    excluded = excluded_category_ids(db)
    income, expense = _totals(db, previous, excluded)

    grouped = db.execute(
        apply_exclusion(
            apply_filters(
                select(Transaction.category_id, func.sum(Transaction.amount)), previous
            ),
            previous,
            excluded,
        ).group_by(Transaction.category_id)
    ).all()

    return {
        "date_from": previous.date_from,
        "date_to": previous.date_to,
        "income": income,
        "expense": expense,
        "net": income + expense,
        "by_category": {
            (category_id if category_id is not None else 0): Decimal(total or 0)
            for category_id, total in grouped
        },
    }


def by_month(db: Session, filters: TxFilters, months: int = 12) -> list[dict]:
    excluded = excluded_category_ids(db)
    query = apply_exclusion(
        apply_filters(
            select(
                func.date_format(Transaction.booked_at, "%Y-%m").label("month"),
                _POSITIVE,
                _NEGATIVE,
            ),
            filters,
        ),
        filters,
        excluded,
    ).group_by("month").order_by("month")

    rows = [
        {
            "month": month,
            "income": Decimal(income or 0),
            "expense": Decimal(expense or 0),
            "net": Decimal(income or 0) + Decimal(expense or 0),
        }
        for month, income, expense in db.execute(query).all()
    ]
    return rows[-months:]


def top_expenses(db: Session, filters: TxFilters, limit: int = 10) -> list[dict]:
    excluded = excluded_category_ids(db)
    names = {c.id: c.name for c in db.scalars(select(Category))}
    query = (
        apply_exclusion(
            apply_filters(select(Transaction).where(Transaction.amount < 0), filters),
            filters,
            excluded,
        )
        .order_by(Transaction.amount.asc())
        .limit(limit)
    )
    return [
        {
            "id": t.id,
            "booked_at": t.booked_at,
            "amount": t.amount,
            "description": t.description,
            "category": names.get(t.category_id),
        }
        for t in db.scalars(query)
    ]


def account_balances(db: Session) -> list[dict]:
    out = []
    for account in db.scalars(select(Account).where(Account.archived == False)):  # noqa: E712
        balance, count = db.execute(
            select(
                func.coalesce(func.sum(Transaction.amount), 0),
                func.count(Transaction.id),
            ).where(Transaction.account_id == account.id)
        ).one()
        out.append(
            {
                "id": account.id,
                "name": account.name,
                "type": account.type,
                "currency": account.currency,
                "balance": Decimal(balance),
                "transactions": count,
            }
        )
    return out
