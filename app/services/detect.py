"""Rilevamenti automatici: abbonamenti ricorrenti e giroconti fra conti propri.

Entrambi cercano di dedurre qualcosa che nei dati non è scritto. Perciò:
**propongono, non decidono.** L'utente vede cosa è stato trovato e conferma.
Un'app che ricategorizza da sola senza chiedere è un'app di cui non ti fidi
più quando i numeri sembrano strani.
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
# abbonamenti ricorrenti
# --------------------------------------------------------------------------

# Fasce di cadenza, con tolleranza: le banche registrano l'addebito con
# qualche giorno di scarto e i mesi non hanno tutti la stessa lunghezza.
CADENCES = [
    ("weekly", 6, 8, Decimal("4.333")),
    ("monthly", 25, 36, Decimal(1)),
    ("quarterly", 85, 96, Decimal("0.333")),
    ("yearly", 350, 380, Decimal("0.0833")),
]

# Quanto può oscillare l'importo restando "lo stesso abbonamento".
AMOUNT_TOLERANCE = Decimal("0.15")


def _cadence_for(gap: float) -> tuple[str, Decimal] | None:
    for name, low, high, factor in CADENCES:
        if low <= gap <= high:
            return name, factor
    return None


def _cluster_by_amount(items: list[Transaction]) -> list[list[Transaction]]:
    """Dentro lo stesso negozio, importi diversi sono cose diverse.

    Discord ha 9,99 il 9 di ogni mese (abbonamento) mescolato a 10,13, 3,49 e
    2,99 (acquisti singoli). Raggruppando tutto insieme la varianza fa
    scartare anche l'abbonamento vero. Separando per importo, resta.

    Un aumento di prezzo spezza il gruppo in due: entrambi i tronconi vengono
    comunque valutati, quindi al massimo si perde un po' di storico.
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
    """La sequenza regolare più recente, risalendo dall'ultimo addebito.

    Un intervallo è accettato se vale circa 1, 2 o 3 volte la cadenza: così un
    mese saltato non spezza la serie, mentre un vuoto di nove mesi sì — perché
    quello è un abbonamento diverso, disdetto e poi riattivato.
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
    """Un abbonamento addebita sempre lo stesso giorno.

    È il discriminante che separa un abbonamento da una coincidenza. Senza,
    tre acquisti Steam da ~40 € capitati a tre mesi di distanza vengono
    scambiati per un abbonamento trimestrale: importo simile e cadenza
    plausibile, ma i giorni sono 25, 24 e 14. Spotify è sempre il 14,
    Discord sempre il 9, Claude sempre il 4.
    """
    if cadence == "weekly":
        days = [t.booked_at.weekday() for t in items]
        reference = median(days)
        return all(min(abs(d - reference), 7 - abs(d - reference)) <= 1 for d in days)

    days = [t.booked_at.day for t in items]
    reference = median(days)
    # tolleranza di 3 giorni: weekend e festivi spostano l'addebito
    return all(min(abs(d - reference), 31 - abs(d - reference)) <= 3 for d in days)


def detect_subscriptions(
    db: Session,
    *,
    min_occurrences: int = 3,
    months_back: int = 18,
) -> dict:
    """Trova gli addebiti che si ripetono a intervalli regolari e importo stabile.

    Servono **entrambe** le condizioni. Solo la regolarità non basta: Amazon
    compare ogni mese ma con importi da 6 a 1.157 €, e non è un abbonamento.
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

        # Si guarda solo la sequenza regolare più recente, non tutta la storia.
        # Un abbonamento vero ha buchi: pagamenti falliti, mesi in pausa,
        # disdette e riattivazioni. Discord è addebitato il 9 di ogni mese ma
        # ha un vuoto di 9 mesi nel 2025: pretendendo che *ogni* intervallo sia
        # regolare, l'abbonamento più evidente verrebbe scartato.
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
        # attivo se il prossimo addebito non è già in forte ritardo
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
# giroconti fra conti propri
# --------------------------------------------------------------------------


def detect_transfers(db: Session, *, window_days: int = 5) -> list[dict]:
    """Cerca coppie di movimenti uguali e opposti su due conti diversi.

    Sono gli stessi soldi che si spostano: contarli come spesa e come entrata
    gonfia i totali due volte, spesso per migliaia di euro l'anno.

    L'abbinamento è **1 a 1**: una volta accoppiato, un movimento non può
    essere riusato. Senza questo, tre addebiti da 3,50 € genererebbero nove
    coppie invece di una.
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
    """Assegna la categoria trasferimenti alle coppie rilevate.

    Le transazioni categorizzate a mano non vengono toccate: vale qui come
    ovunque nel progetto.
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
