"""Motore di categorizzazione a regole.

Due principi non negoziabili:

1. **Le regole le scrive l'utente.** Nessuna categoria indovinata da una lista
   di negozi hardcodata: si romperebbe fuori dall'Italia, invecchierebbe, e
   non sarebbe modificabile da chi si autohosta.
2. **Una categoria scelta a mano non si tocca mai.** Se hai corretto una
   transazione, nessuna riapplicazione delle regole può sovrascriverti.
   È il senso della colonna `category_source`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, CategoryRule, Transaction

MAX_PATTERN_LENGTH = 255


class InvalidRule(ValueError):
    pass


def validate_pattern(match_type: str, pattern: str) -> None:
    if not pattern or not pattern.strip():
        raise InvalidRule("Il pattern non può essere vuoto")
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise InvalidRule(f"Pattern troppo lungo (massimo {MAX_PATTERN_LENGTH} caratteri)")
    if match_type == "regex":
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise InvalidRule(f"Espressione regolare non valida: {exc}") from None


def _matches(rule: CategoryRule, text: str) -> bool:
    if not text:
        return False
    haystack = text.lower()
    needle = rule.pattern.lower()

    if rule.match_type == "contains":
        return needle in haystack
    if rule.match_type == "starts_with":
        return haystack.startswith(needle)
    if rule.match_type == "exact":
        return haystack.strip() == needle.strip()
    if rule.match_type == "regex":
        try:
            return re.search(rule.pattern, text, re.IGNORECASE) is not None
        except re.error:
            # regola diventata invalida: la si ignora invece di far fallire
            # l'intera riapplicazione
            return False
    return False


def _field_value(transaction: Transaction, field_name: str) -> str:
    if field_name == "counterparty":
        return transaction.counterparty or ""
    return transaction.description or ""


def match_rule(transaction: Transaction, rules: list[CategoryRule]) -> CategoryRule | None:
    """Prima regola che combacia, in ordine di priorità.

    `priority` più basso = valutata prima = vince. Serve un ordine
    deterministico: le regole si sovrappongono quasi sempre ("amazon" e
    "amazon prime") e senza priorità il risultato dipenderebbe dal caso.
    """
    for rule in rules:
        if rule.account_id is not None and rule.account_id != transaction.account_id:
            continue
        if _matches(rule, _field_value(transaction, rule.field)):
            return rule
    return None


def load_rules(db: Session) -> list[CategoryRule]:
    return list(
        db.scalars(
            select(CategoryRule)
            .where(CategoryRule.enabled == True)  # noqa: E712
            .order_by(CategoryRule.priority, CategoryRule.id)
        )
    )


@dataclass
class ApplyResult:
    examined: int = 0
    matched: int = 0
    updated: int = 0
    protected: int = 0  # scelte a mano, lasciate stare
    by_category: dict[str, int] = field(default_factory=dict)
    samples: list[dict] = field(default_factory=list)


def apply_rules(
    db: Session,
    *,
    account_id: int | None = None,
    only_uncategorized: bool = True,
    dry_run: bool = False,
    sample_limit: int = 20,
) -> ApplyResult:
    """Applica le regole alle transazioni esistenti.

    `only_uncategorized=True` tocca solo le righe senza categoria.
    Con `False` ricategorizza anche quelle già assegnate **da una regola**,
    utile dopo aver cambiato le regole — ma le scelte manuali restano
    comunque intoccabili.
    """
    rules = load_rules(db)
    result = ApplyResult()
    if not rules:
        return result

    categories = {c.id: c.name for c in db.scalars(select(Category))}

    query = select(Transaction)
    if account_id is not None:
        query = query.where(Transaction.account_id == account_id)
    if only_uncategorized:
        query = query.where(Transaction.category_id.is_(None))

    counts: dict[str, int] = defaultdict(int)

    for transaction in db.scalars(query):
        result.examined += 1

        # una categoria scelta a mano non si tocca, mai
        if transaction.category_source == "manual":
            result.protected += 1
            continue

        rule = match_rule(transaction, rules)
        if rule is None:
            continue

        result.matched += 1
        if transaction.category_id == rule.category_id:
            continue  # già a posto

        if len(result.samples) < sample_limit:
            result.samples.append(
                {
                    "transaction_id": transaction.id,
                    "booked_at": transaction.booked_at.isoformat(),
                    "amount": str(transaction.amount),
                    "description": transaction.description,
                    "from_category": categories.get(transaction.category_id),
                    "to_category": categories.get(rule.category_id),
                    "rule_id": rule.id,
                    "rule_pattern": rule.pattern,
                }
            )

        counts[categories.get(rule.category_id, "?")] += 1
        result.updated += 1

        if not dry_run:
            transaction.category_id = rule.category_id
            transaction.category_source = "rule"

    result.by_category = dict(counts)

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return result


# --------------------------------------------------------------------------
# suggerimenti
# --------------------------------------------------------------------------

_STRIP_PARENS = re.compile(r"\([^)]*\)")
_STRIP_STARRED = re.compile(r"\*\S*")
_STRIP_DIGITS = re.compile(r"\d+")
_COLLAPSE = re.compile(r"\s+")


def merchant_key(description: str | None) -> str:
    """Riduce una descrizione al "negozio" che ci sta dietro.

    "Top-up by *1234" e "Top-up by *5678" collassano sulla stessa chiave, così
    diventano una regola sola invece di due.
    """
    text = (description or "").lower()
    text = _STRIP_PARENS.sub(" ", text)
    text = _STRIP_STARRED.sub(" ", text)
    text = _STRIP_DIGITS.sub(" ", text)
    text = re.sub(r"[^\w\s&.'-]", " ", text)
    return _COLLAPSE.sub(" ", text).strip()


_TRAILING_NOISE = re.compile(r"^[*#]?\d[\d./-]*$")


def suggest_pattern(description: str | None) -> str:
    """Un pattern che sia davvero **contenuto** nella descrizione.

    `merchant_key` serve a raggruppare e per farlo toglie le cifre: da
    "G2a Com" ricava "g a com", che come testo da cercare non trova nulla.
    Qui invece si tolgono solo i pezzi di coda variabili — codici carta,
    numeri di scontrino, IBAN fra parentesi — lasciando una stringa che il
    matching "contiene" trova per davvero.

        "Bottega 4821"            -> "bers"
        "Top-up by *1234"       -> "top-up by"
        "G2a Com"               -> "g2a com"
        "Incoming from X (IT..)"-> "incoming from x"
    """
    text = _STRIP_PARENS.sub(" ", (description or "")).strip().lower()
    tokens = [t for t in _COLLAPSE.sub(" ", text).split(" ") if t]

    while tokens and _TRAILING_NOISE.match(tokens[-1]):
        tokens.pop()

    pattern = " ".join(tokens).strip(" -_.,;:")
    # se restasse troppo poco per essere selettivo, meglio la descrizione intera
    if len(pattern) < 3:
        return _COLLAPSE.sub(" ", (description or "").strip().lower())
    return pattern


def count_matching(
    db: Session,
    *,
    pattern: str,
    match_type: str = "contains",
    field: str = "description",
    account_id: int | None = None,
) -> dict:
    """Quante transazioni prenderebbe una regola, e quante ne cambierebbe.

    Serve a poter dire "vale anche per gli altri 29" invece di far creare una
    regola alla cieca.
    """
    probe = CategoryRule()
    probe.pattern = pattern
    probe.match_type = match_type
    probe.field = field
    probe.account_id = account_id

    query = select(Transaction)
    if account_id is not None:
        query = query.where(Transaction.account_id == account_id)

    total = 0
    changeable = 0
    samples: list[str] = []
    for transaction in db.scalars(query):
        if not _matches(probe, _field_value(transaction, field)):
            continue
        total += 1
        if transaction.category_source != "manual":
            changeable += 1
        if len(samples) < 5:
            samples.append(transaction.description)

    return {"pattern": pattern, "total": total, "changeable": changeable, "samples": samples}


def suggest_rules(db: Session, *, limit: int = 30, account_id: int | None = None) -> list[dict]:
    """Raggruppa le transazioni senza categoria per negozio ricorrente.

    Serve a partire: con 400 transazioni da categorizzare a mano si molla dopo
    dieci minuti. Raggruppate, diventano ~20 regole da creare una volta sola.
    """
    query = select(Transaction).where(Transaction.category_id.is_(None))
    if account_id is not None:
        query = query.where(Transaction.account_id == account_id)

    groups: dict[str, dict] = {}
    for transaction in db.scalars(query):
        key = merchant_key(transaction.description)
        if not key:
            continue
        group = groups.setdefault(
            key,
            {
                # il pattern proposto deve essere cercabile, non la chiave di
                # raggruppamento: vedi suggest_pattern()
                "pattern": suggest_pattern(transaction.description),
                "count": 0,
                "total": Decimal(0),
                "samples": [],
            },
        )
        group["count"] += 1
        group["total"] += transaction.amount
        if len(group["samples"]) < 3:
            group["samples"].append(transaction.description)

    ordered = sorted(groups.values(), key=lambda g: (-g["count"], g["pattern"]))
    return [
        {
            "pattern": g["pattern"],
            "count": g["count"],
            "total": str(g["total"]),
            "samples": g["samples"],
        }
        for g in ordered[:limit]
    ]
