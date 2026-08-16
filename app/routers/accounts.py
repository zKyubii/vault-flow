"""Conti e categorie."""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Account, Category, Transaction
from app.schemas import (
    AccountCreate,
    AccountUpdate,
    AccountWithBalance,
    CategoryCreate,
    CategoryOut,
    CategoryUpdate,
    Message,
)

router = APIRouter(tags=["conti"])


@router.get("/accounts", response_model=list[AccountWithBalance])
def list_accounts(include_archived: bool = False, db: Session = Depends(get_db)):
    query = select(Account).order_by(Account.name)
    if not include_archived:
        query = query.where(Account.archived == False)  # noqa: E712

    out = []
    for account in db.scalars(query):
        balance, count = db.execute(
            select(
                func.coalesce(func.sum(Transaction.amount), 0),
                func.count(Transaction.id),
            ).where(Transaction.account_id == account.id)
        ).one()
        out.append(
            AccountWithBalance(
                id=account.id,
                name=account.name,
                type=account.type,
                currency=account.currency,
                iban=account.iban,
                archived=bool(account.archived),
                balance=Decimal(balance),
                transactions=count,
            )
        )
    return out


@router.post("/accounts", response_model=AccountWithBalance, status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)):
    account = Account(
        name=payload.name.strip(),
        type=payload.type,
        currency=payload.currency.upper(),
        iban=(payload.iban or None),
    )
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"An account named '{payload.name}' already exists")
    db.refresh(account)
    return AccountWithBalance(
        id=account.id,
        name=account.name,
        type=account.type,
        currency=account.currency,
        iban=account.iban,
        archived=False,
        balance=Decimal(0),
        transactions=0,
    )


@router.patch("/accounts/{account_id}", response_model=AccountWithBalance)
def update_account(account_id: int, payload: AccountUpdate, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Account name already in use")
    db.refresh(account)

    balance, count = db.execute(
        select(
            func.coalesce(func.sum(Transaction.amount), 0),
            func.count(Transaction.id),
        ).where(Transaction.account_id == account.id)
    ).one()
    return AccountWithBalance(
        id=account.id,
        name=account.name,
        type=account.type,
        currency=account.currency,
        iban=account.iban,
        archived=bool(account.archived),
        balance=Decimal(balance),
        transactions=count,
    )


@router.delete("/accounts/{account_id}", response_model=Message)
def delete_account(account_id: int, db: Session = Depends(get_db)):
    """Elimina un conto **e tutti i suoi movimenti** (FK ON DELETE CASCADE).

    È distruttivo e non si torna indietro: la risposta dice quante transazioni
    sono state rimosse, e l'interfaccia deve chiedere conferma mostrando il
    numero *prima* di chiamare qui. Per far solo sparire un conto dagli
    elenchi senza perdere niente esiste `archived`.
    """
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")

    count = db.scalar(
        select(func.count(Transaction.id)).where(Transaction.account_id == account_id)
    )
    name = account.name
    db.delete(account)
    db.commit()
    return Message(
        detail=f"Account '{name}' deleted along with {count} transactions",
        data={"deleted_transactions": count},
    )


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    return list(db.scalars(select(Category).order_by(Category.parent_id, Category.name)))


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(payload: CategoryCreate, db: Session = Depends(get_db)):
    if payload.parent_id is not None and not db.get(Category, payload.parent_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent category not found")

    existing = db.scalar(
        select(Category.id).where(
            Category.name == payload.name.strip(),
            Category.parent_id.is_(payload.parent_id)
            if payload.parent_id is None
            else Category.parent_id == payload.parent_id,
        )
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A category named '{payload.name}' already exists")

    category = Category(**{**payload.model_dump(), "name": payload.name.strip()})
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.patch("/categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: int, payload: CategoryUpdate, db: Session = Depends(get_db)):
    category = db.get(Category, category_id)
    if not category:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")

    changes = payload.model_dump(exclude_unset=True)
    if changes.get("parent_id") == category_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "A category cannot be its own parent"
        )
    for field, value in changes.items():
        setattr(category, field, value)
    db.commit()
    db.refresh(category)
    return category


@router.delete("/categories/{category_id}", response_model=Message)
def delete_category(category_id: int, db: Session = Depends(get_db)):
    """Le transazioni non si perdono: la FK è ON DELETE SET NULL, tornano
    semplicemente senza categoria."""
    category = db.get(Category, category_id)
    if not category:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")

    used = db.scalar(
        select(func.count(Transaction.id)).where(Transaction.category_id == category_id)
    )
    db.delete(category)
    db.commit()
    return Message(
        detail=f"Category deleted. {used} transactions are now uncategorised."
    )
