"""Aggregazioni per la dashboard.

Le somme si fanno nel database, non nel browser: a 400 transazioni la
differenza non si vede, a 20.000 sì. E il telefono non deve scaricare tutto
lo storico per mostrare un totale.

Le categorie con `exclude_from_stats` (giroconti fra conti propri, depositi su
broker, saldi iniziali) restano nei saldi ma **non** entrano in entrate e
uscite: sono soldi spostati, non guadagnati né spesi.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.filters import TxFilters, apply_exclusion, apply_filters, excluded_category_ids
from app.models import Account, Category, Transaction

# Entrate e uscite si calcolano **per transazione**, non sommando prima per
# categoria: un rimborso dentro "Shopping" annullerebbe parte degli acquisti e
# sparirebbe da entrambi i totali. Chi guarda vuole sapere quanto è uscito e
# quanto è entrato, non il saldo netto di ogni categoria.
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

    # Soldi spostati fra conti propri: si conta solo il lato in uscita,
    # altrimenti ogni giroconto verrebbe contato due volte.
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
            "name": names[category_id].name if category_id in names else "Senza categoria",
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
    """Il periodo da confrontare.

    Se l'intervallo è **esattamente un mese di calendario**, il precedente è il
    mese di calendario prima — non "gli N giorni prima". Confrontare luglio
    (31 giorni) con "30 giugno più il 31 maggio" darebbe un numero giusto in
    astratto e sbagliato nella testa di chi guarda.
    """
    is_full_month = start.day == 1 and end == _last_day_of_month(start) and start.month == end.month
    if is_full_month:
        previous_end = start - timedelta(days=1)
        return previous_end.replace(day=1), previous_end
    return start - timedelta(days=span), start - timedelta(days=1)


def compare_previous(db: Session, filters: TxFilters) -> dict | None:
    """Confronta il periodo con quello immediatamente precedente di pari durata.

    Senza un termine di paragone "hai speso 800 €" non dice niente: il punto è
    sapere se sono tanti *per te*.
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
