"""Filters shared between transactions and statistics.

One place defines and applies them: if the summary and the list filtered even
slightly differently, the totals would not match the rows underneath them —
and that is the kind of inconsistency that destroys trust in the numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from fastapi import Query
from sqlalchemy import Select, or_, select

from app.models import Category, Transaction

Kind = Literal["all", "income", "expense"]


@dataclass(slots=True)
class TxFilters:
    date_from: date | None = None
    date_to: date | None = None
    account_ids: list[int] | None = None
    category_ids: list[int] | None = None
    kind: Kind = "all"
    search: str | None = None
    uncategorized: bool = False


def tx_filters(
    date_from: date | None = Query(None, description="From this day (inclusive)"),
    date_to: date | None = Query(None, description="To this day (inclusive)"),
    account_ids: list[int] | None = Query(None, description="One or more accounts"),
    category_ids: list[int] | None = Query(None, description="One or more categories"),
    kind: Kind = Query("all", description="Income only, expenses only, or everything"),
    search: str | None = Query(None, description="Text in the description"),
    uncategorized: bool = Query(False, description="Only the ones without a category"),
) -> TxFilters:
    return TxFilters(
        date_from=date_from,
        date_to=date_to,
        account_ids=account_ids or None,
        category_ids=category_ids or None,
        kind=kind,
        search=(search or "").strip() or None,
        uncategorized=uncategorized,
    )


def excluded_category_ids(db) -> set[int]:
    return set(
        db.scalars(
            select(Category.id).where(Category.exclude_from_stats == True)  # noqa: E712
        )
    )


def apply_filters(query: Select, filters: TxFilters) -> Select:
    if filters.date_from is not None:
        query = query.where(Transaction.booked_at >= filters.date_from)
    if filters.date_to is not None:
        query = query.where(Transaction.booked_at <= filters.date_to)
    if filters.account_ids:
        query = query.where(Transaction.account_id.in_(filters.account_ids))
    if filters.uncategorized:
        query = query.where(Transaction.category_id.is_(None))
    elif filters.category_ids:
        query = query.where(Transaction.category_id.in_(filters.category_ids))
    if filters.kind == "income":
        query = query.where(Transaction.amount > 0)
    elif filters.kind == "expense":
        query = query.where(Transaction.amount < 0)
    if filters.search:
        pattern = f"%{filters.search}%"
        query = query.where(
            or_(Transaction.description.like(pattern), Transaction.counterparty.like(pattern))
        )
    return query


def apply_exclusion(query: Select, filters: TxFilters, excluded: set[int]) -> Select:
    """Removes transfers and the like from the totals.

    **Unless the user asked for them explicitly**: if you select the
    "Transfers" category in the filter you want to see it — hiding it would be
    the app contradicting your click.
    """
    if not excluded or filters.category_ids:
        return query
    return query.where(
        or_(
            Transaction.category_id.is_(None),
            Transaction.category_id.notin_(excluded),
        )
    )
