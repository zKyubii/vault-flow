"""Aggregations for the dashboard. Every endpoint accepts the same filters as
`/transactions`, so the totals always match the list."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.filters import TxFilters, tx_filters
from app.services import stats as stats_service

router = APIRouter(tags=["statistics"])


@router.get("/stats/summary")
def summary(
    compare: bool = Query(False, description="Include the comparison with the previous period"),
    filters: TxFilters = Depends(tx_filters),
    db: Session = Depends(get_db),
):
    data = stats_service.summary(db, filters)
    if compare:
        data["previous"] = stats_service.compare_previous(db, filters)
    return data


@router.get("/stats/months")
def months(
    months: int = Query(default=12, ge=1, le=60),
    filters: TxFilters = Depends(tx_filters),
    db: Session = Depends(get_db),
):
    return stats_service.by_month(db, filters, months=months)


@router.get("/stats/top")
def top(
    limit: int = Query(default=10, ge=1, le=50),
    filters: TxFilters = Depends(tx_filters),
    db: Session = Depends(get_db),
):
    return stats_service.top_expenses(db, filters, limit=limit)


@router.get("/stats/balances")
def balances(db: Session = Depends(get_db)):
    return stats_service.account_balances(db)
