"""Filtri condivisi fra movimenti e statistiche.

Un solo posto che li definisce e li applica: se il riepilogo e la lista
filtrassero in modo anche leggermente diverso, i totali non corrisponderebbero
a ciò che si vede sotto — ed è il tipo di incoerenza che fa perdere fiducia
nei numeri.
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
    date_from: date | None = Query(None, description="Dal giorno (compreso)"),
    date_to: date | None = Query(None, description="Al giorno (compreso)"),
    account_ids: list[int] | None = Query(None, description="Uno o più conti"),
    category_ids: list[int] | None = Query(None, description="Una o più categorie"),
    kind: Kind = Query("all", description="Solo entrate, solo uscite, o tutto"),
    search: str | None = Query(None, description="Testo nella descrizione"),
    uncategorized: bool = Query(False, description="Solo quelle senza categoria"),
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
    """Toglie giroconti e simili dai totali.

    **Salvo che l'utente li abbia chiesti esplicitamente**: se selezioni la
    categoria "Trasferimenti" nel filtro, vuoi vederla — nasconderla sarebbe
    l'app che contraddice il tuo click.
    """
    if not excluded or filters.category_ids:
        return query
    return query.where(
        or_(
            Transaction.category_id.is_(None),
            Transaction.category_id.notin_(excluded),
        )
    )
