"""Rilevamenti automatici: abbonamenti e giroconti."""

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Category
from app.services import detect as detect_service

router = APIRouter(tags=["rilevamenti"])


@router.get("/detect/subscriptions")
def subscriptions(
    min_occurrences: int = Query(default=3, ge=2, le=12),
    months_back: int = Query(default=18, ge=1, le=120),
    db: Session = Depends(get_db),
):
    """Addebiti ricorrenti a cadenza e importo regolari.

    Servono entrambe le condizioni: Amazon compare ogni mese ma con importi da
    6 a 1.157 €, e non è un abbonamento.
    """
    return detect_service.detect_subscriptions(
        db, min_occurrences=min_occurrences, months_back=months_back
    )


@router.get("/detect/transfers")
def transfers(
    window_days: int = Query(default=5, ge=0, le=30),
    db: Session = Depends(get_db),
):
    """Coppie di movimenti uguali e opposti su conti diversi. Solo proposta."""
    return detect_service.detect_transfers(db, window_days=window_days)


@router.post("/detect/transfers/apply")
def apply_transfers(
    category_id: int = Body(..., embed=True),
    window_days: int = Body(5, embed=True),
    transaction_ids: list[int] | None = Body(None, embed=True),
    db: Session = Depends(get_db),
):
    """Marca le coppie rilevate con la categoria indicata.

    `transaction_ids` limita l'operazione a movimenti specifici; omesso, vale
    per tutte le coppie trovate. Le categorie scelte a mano restano intoccate.
    """
    category = db.get(Category, category_id)
    if not category:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Categoria inesistente")
    if not category.exclude_from_stats:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"'{category.name}' non è esclusa dalle statistiche: marcare dei "
            "giroconti con una categoria che conta come spesa li farebbe "
            "contare due volte, che è il problema che stiamo risolvendo.",
        )

    return detect_service.apply_transfers(
        db,
        category_id=category_id,
        window_days=window_days,
        pair_ids=transaction_ids,
    )
