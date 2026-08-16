"""Transazioni: elenco e inserimento manuale."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.filters import TxFilters, apply_filters, tx_filters
from app.importers.csv_importer import compute_dedup_hash
from app.models import Account, Category, Transaction
from app.schemas import (
    Message,
    SetCategoryRequest,
    TransactionCreate,
    TransactionOut,
    TransactionPage,
)

router = APIRouter(tags=["transazioni"])


@router.get("/transactions", response_model=TransactionPage)
def list_transactions(
    filters: TxFilters = Depends(tx_filters),
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Elenco filtrato.

    Usa gli stessi filtri di `/stats/*`: i totali del riepilogo e le righe qui
    sotto vengono sempre dalla stessa selezione. Nota: qui i giroconti **non**
    sono esclusi — la lista mostra i movimenti veri del conto, l'esclusione
    riguarda solo i totali di entrate e uscite.
    """
    total = db.scalar(apply_filters(select(func.count(Transaction.id)), filters)) or 0
    items = db.scalars(
        apply_filters(select(Transaction), filters)
        .order_by(Transaction.booked_at.desc(), Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return TransactionPage(total=total, items=list(items))


@router.post(
    "/transactions", response_model=TransactionOut, status_code=status.HTTP_201_CREATED
)
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db)):
    """Inserimento manuale.

    Serve per il contante, che non lascia traccia digitale da importare.
    Deve restare veloce: gli unici campi obbligatori sono conto, data,
    importo e descrizione.
    """
    if not db.get(Account, payload.account_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conto inesistente")
    if payload.category_id is not None and not db.get(Category, payload.category_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Categoria inesistente")
    if payload.amount == 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "L'importo non può essere zero")

    # Su inserimento manuale il "duplicato" è quasi sempre un doppio invio del
    # form, non un dato vero: si cerca il primo slot di occorrenza libero.
    occurrence = 0
    while occurrence < 100:
        candidate = compute_dedup_hash(
            booked_at=payload.booked_at,
            amount=payload.amount,
            description=payload.description,
            occurrence=occurrence,
        )
        exists = db.scalar(
            select(Transaction.id).where(
                Transaction.account_id == payload.account_id,
                Transaction.dedup_hash == candidate,
            )
        )
        if not exists:
            break
        occurrence += 1

    transaction = Transaction(
        account_id=payload.account_id,
        booked_at=payload.booked_at,
        amount=payload.amount,
        currency=payload.currency.upper(),
        description=payload.description.strip(),
        counterparty=payload.counterparty,
        category_id=payload.category_id,
        category_source="manual" if payload.category_id else None,
        source="manual",
        dedup_hash=candidate,
        notes=payload.notes,
    )
    db.add(transaction)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Transazione già presente")
    db.refresh(transaction)
    return transaction


@router.put("/transactions/{transaction_id}/category", response_model=TransactionOut)
def set_category(
    transaction_id: int, payload: SetCategoryRequest, db: Session = Depends(get_db)
):
    """Assegna una categoria a mano.

    Marca `category_source='manual'`: da questo momento nessuna
    riapplicazione delle regole potrà sovrascrivere la scelta.
    Passare `category_id: null` rimuove la categoria e libera la riga.
    """
    transaction = db.get(Transaction, transaction_id)
    if not transaction:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transazione inesistente")
    if payload.category_id is not None and not db.get(Category, payload.category_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Categoria inesistente")

    transaction.category_id = payload.category_id
    transaction.category_source = "manual" if payload.category_id is not None else None
    db.commit()
    db.refresh(transaction)
    return transaction


@router.delete("/transactions/{transaction_id}", response_model=Message)
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    transaction = db.get(Transaction, transaction_id)
    if not transaction:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transazione inesistente")
    db.delete(transaction)
    db.commit()
    return Message(detail="Transazione eliminata")
