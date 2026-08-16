"""Rule-based categorisation engine.

Two non-negotiable principles:

1. **The user writes the rules.** No categories guessed from a hardcoded list
   of merchants: it would break outside one country, it would age badly, and
   whoever self-hosts could not change it.
2. **A category chosen by hand is never touched.** If you corrected a
   transaction, no re-run of the rules may overwrite you. That is what the
   `category_source` column is for.
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
        raise InvalidRule("The pattern cannot be empty")
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise InvalidRule(f"Pattern too long (maximum {MAX_PATTERN_LENGTH} characters)")
    if match_type == "regex":
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise InvalidRule(f"Invalid regular expression: {exc}") from None


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
            # a rule that became invalid is ignored rather than failing the
            # whole re-run
            return False
    return False


def _field_value(transaction: Transaction, field_name: str) -> str:
    if field_name == "counterparty":
        return transaction.counterparty or ""
    return transaction.description or ""


def match_rule(transaction: Transaction, rules: list[CategoryRule]) -> CategoryRule | None:
    """First rule that matches, in priority order.

    Lower `priority` = evaluated first = wins. A deterministic order is
    required: rules almost always overlap ("amazon" and "amazon prime") and
    without priority the result would depend on chance.
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
    protected: int = 0  # set by hand, left alone
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
    """Applies the rules to existing transactions.

    `only_uncategorized=True` touches only rows without a category.
    With `False` it also re-categorises rows previously assigned **by a
    rule**, which is useful after changing the rules — but manual choices stay
    untouchable either way.
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

        # a category chosen by hand is never touched
        if transaction.category_source == "manual":
            result.protected += 1
            continue

        rule = match_rule(transaction, rules)
        if rule is None:
            continue

        result.matched += 1
        if transaction.category_id == rule.category_id:
            continue  # already correct

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
# suggestions
# --------------------------------------------------------------------------

_STRIP_PARENS = re.compile(r"\([^)]*\)")
_STRIP_STARRED = re.compile(r"\*\S*")
_STRIP_DIGITS = re.compile(r"\d+")
_COLLAPSE = re.compile(r"\s+")


def merchant_key(description: str | None) -> str:
    """Reduces a description to the merchant behind it.

    "Top-up by *3208" and "Top-up by *4140" collapse onto the same key, so
    they become one rule instead of two.
    """
    text = (description or "").lower()
    text = _STRIP_PARENS.sub(" ", text)
    text = _STRIP_STARRED.sub(" ", text)
    text = _STRIP_DIGITS.sub(" ", text)
    text = re.sub(r"[^\w\s&.'-]", " ", text)
    return _COLLAPSE.sub(" ", text).strip()


_TRAILING_NOISE = re.compile(r"^[*#]?\d[\d./-]*$")


def suggest_pattern(description: str | None) -> str:
    """A pattern that is genuinely **contained** in the description.

    `merchant_key` exists to group, and to do that it strips digits: from
    "G2a Com" it produces "g a com", which as a search string matches
    nothing. Here we only remove the variable trailing parts — card codes,
    receipt numbers, IBANs in brackets — leaving a string that a "contains"
    match will actually find.

        "Bers 14700"            -> "bers"
        "Top-up by *3208"       -> "top-up by"
        "G2a Com"               -> "g2a com"
        "Incoming from X (IT..)"-> "incoming from x"
    """
    text = _STRIP_PARENS.sub(" ", (description or "")).strip().lower()
    tokens = [t for t in _COLLAPSE.sub(" ", text).split(" ") if t]

    while tokens and _TRAILING_NOISE.match(tokens[-1]):
        tokens.pop()

    pattern = " ".join(tokens).strip(" -_.,;:")
    # if too little would be left to be selective, prefer the full description
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
    """How many transactions a rule would catch, and how many it would change.

    This is what lets the app say "it also applies to the other 29" instead of
    making you create a rule blind.
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
    """Groups uncategorised transactions by recurring merchant.

    It exists to get you started: categorising 400 transactions by hand is
    abandoned after ten minutes. Grouped, they become around 20 rules you
    create once.
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
                # the suggested pattern must be searchable, not the grouping
                # key: see suggest_pattern()
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
