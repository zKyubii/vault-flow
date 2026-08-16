"""Categorisation rules."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account, Category, CategoryRule, Transaction
from app.schemas import (
    ApplyRulesRequest,
    ApplyRulesResponse,
    CategoryRuleCreate,
    CategoryRuleOut,
    CategoryRuleUpdate,
    Message,
    RuleFromTransaction,
    RuleSuggestion,
)
from app.services.categorization import (
    InvalidRule,
    apply_rules,
    count_matching,
    suggest_pattern,
    suggest_rules,
    validate_pattern,
)

router = APIRouter(tags=["categorisation"])


def _check_refs(db: Session, category_id: int | None, account_id: int | None) -> None:
    if category_id is not None and not db.get(Category, category_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    if account_id is not None and not db.get(Account, account_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")


@router.get("/rules", response_model=list[CategoryRuleOut])
def list_rules(db: Session = Depends(get_db)):
    return list(
        db.scalars(select(CategoryRule).order_by(CategoryRule.priority, CategoryRule.id))
    )


@router.post("/rules", response_model=CategoryRuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(payload: CategoryRuleCreate, db: Session = Depends(get_db)):
    _check_refs(db, payload.category_id, payload.account_id)
    try:
        validate_pattern(payload.match_type, payload.pattern)
    except InvalidRule as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    rule = CategoryRule(**payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.patch("/rules/{rule_id}", response_model=CategoryRuleOut)
def update_rule(rule_id: int, payload: CategoryRuleUpdate, db: Session = Depends(get_db)):
    rule = db.get(CategoryRule, rule_id)
    if not rule:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")

    changes = payload.model_dump(exclude_unset=True)
    _check_refs(db, changes.get("category_id"), changes.get("account_id"))
    try:
        validate_pattern(
            changes.get("match_type", rule.match_type),
            changes.get("pattern", rule.pattern),
        )
    except InvalidRule as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    for field_name, value in changes.items():
        setattr(rule, field_name, value)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}", response_model=Message)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(CategoryRule, rule_id)
    if not rule:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    db.delete(rule)
    db.commit()
    return Message(detail="Rule deleted")


@router.post("/rules/apply", response_model=ApplyRulesResponse)
def apply(payload: ApplyRulesRequest, db: Session = Depends(get_db)):
    """Applies the rules. A **dry run** by default: nothing is written.

    Transactions with `category_source='manual'` are never touched, not even
    with `only_uncategorized=False`.
    """
    if payload.account_id is not None and not db.get(Account, payload.account_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")

    result = apply_rules(
        db,
        account_id=payload.account_id,
        only_uncategorized=payload.only_uncategorized,
        dry_run=payload.dry_run,
    )
    return ApplyRulesResponse(
        dry_run=payload.dry_run,
        examined=result.examined,
        matched=result.matched,
        updated=result.updated,
        protected=result.protected,
        by_category=result.by_category,
        samples=result.samples,
    )


@router.get("/transactions/{transaction_id}/similar")
def similar(transaction_id: int, db: Session = Depends(get_db)):
    """How many transactions look like this one, and what text finds them.

    This is what makes it possible to ask "does it apply to the other 29 too?"
    at the moment you are already choosing the category, instead of sending
    you to a separate screen to write a pattern by hand.
    """
    transaction = db.get(Transaction, transaction_id)
    if not transaction:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found")

    pattern = suggest_pattern(transaction.description)
    result = count_matching(db, pattern=pattern)
    # excluding the one being looked at
    result["others"] = max(0, result["total"] - 1)
    return result


@router.post("/rules/from-transaction", response_model=ApplyRulesResponse)
def rule_from_transaction(payload: RuleFromTransaction, db: Session = Depends(get_db)):
    """Creates the rule from a transaction and applies it immediately.

    The originating transaction stays `manual`: you categorised it yourself,
    the rule is for the others.
    """
    transaction = db.get(Transaction, payload.transaction_id)
    if not transaction:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found")
    if not db.get(Category, payload.category_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")

    pattern = (payload.pattern or suggest_pattern(transaction.description)).strip()
    try:
        validate_pattern("contains", pattern)
    except InvalidRule as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    db.add(
        CategoryRule(
            pattern=pattern,
            category_id=payload.category_id,
            match_type="contains",
            field="description",
            priority=payload.priority,
        )
    )
    db.commit()

    result = apply_rules(db, only_uncategorized=not payload.recategorize, dry_run=False)
    return ApplyRulesResponse(
        dry_run=False,
        examined=result.examined,
        matched=result.matched,
        updated=result.updated,
        protected=result.protected,
        by_category=result.by_category,
        samples=result.samples,
    )


@router.get("/rules/suggestions", response_model=list[RuleSuggestion])
def suggestions(
    limit: int = Query(default=30, le=200),
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Recurring merchants among the still-uncategorised transactions.

    Turns "categorise 400 by hand" into "create 20 rules".
    """
    return suggest_rules(db, limit=limit, account_id=account_id)
