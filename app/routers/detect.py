"""Automatic detection: subscriptions and transfers."""

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Category
from app.services import detect as detect_service

router = APIRouter(tags=["detection"])


@router.get("/detect/subscriptions")
def subscriptions(
    min_occurrences: int = Query(default=3, ge=2, le=12),
    months_back: int = Query(default=18, ge=1, le=120),
    db: Session = Depends(get_db),
):
    """Charges that recur with a regular cadence and a regular amount.

    Both conditions are required: a big retailer shows up every month with
    amounts from 6 to 1,157, and that is not a subscription.
    """
    return detect_service.detect_subscriptions(
        db, min_occurrences=min_occurrences, months_back=months_back
    )


@router.get("/detect/transfers")
def transfers(
    window_days: int = Query(default=5, ge=0, le=30),
    db: Session = Depends(get_db),
):
    """Pairs of equal and opposite transactions on different accounts. A
    proposal only."""
    return detect_service.detect_transfers(db, window_days=window_days)


@router.post("/detect/transfers/apply")
def apply_transfers(
    category_id: int = Body(..., embed=True),
    window_days: int = Body(5, embed=True),
    transaction_ids: list[int] | None = Body(None, embed=True),
    db: Session = Depends(get_db),
):
    """Marks the detected pairs with the given category.

    `transaction_ids` limits the operation to specific transactions; omitted,
    it applies to every pair found. Categories chosen by hand are untouched.
    """
    category = db.get(Category, category_id)
    if not category:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    if not category.exclude_from_stats:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"'{category.name}' is not excluded from statistics: marking transfers "
            "with a category that counts as spending would double-count them, "
            "which is the problem being solved here.",
        )

    return detect_service.apply_transfers(
        db,
        category_id=category_id,
        window_days=window_days,
        pair_ids=transaction_ids,
    )
