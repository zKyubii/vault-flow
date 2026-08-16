"""Import profiles and pipeline: inspect → preview → commit → undo."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.importers.csv_importer import parse_file
from app.models import Account, ImportProfile, ImportRun
from app.schemas import (
    ImportProfileCreate,
    ImportProfileOut,
    ImportRunOut,
    InspectResponse,
    Message,
    PreviewError,
    PreviewResponse,
    PreviewRow,
)
from app.services.imports import (
    commit_import,
    existing_hashes,
    inspect_file,
    revert_import,
    summarize,
    to_parse_profile,
)

router = APIRouter(tags=["import"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File too large (maximum {MAX_UPLOAD_BYTES // 1024 // 1024} MB)",
        )
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    return data


def _get_profile(db: Session, profile_id: int) -> ImportProfile:
    profile = db.get(ImportProfile, profile_id)
    if not profile:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import profile not found")
    return profile


def _get_account(db: Session, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    return account


# ---------------------------------------------------------------- profiles


@router.get("/import-profiles", response_model=list[ImportProfileOut])
def list_profiles(db: Session = Depends(get_db)):
    return list(db.scalars(select(ImportProfile).order_by(ImportProfile.name)))


@router.post(
    "/import-profiles", response_model=ImportProfileOut, status_code=status.HTTP_201_CREATED
)
def create_profile(payload: ImportProfileCreate, db: Session = Depends(get_db)):
    if payload.amount_mode == "signed" and not payload.col_amount:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "amount_mode='signed' requires col_amount",
        )
    if payload.amount_mode == "separate" and not (payload.col_amount_in or payload.col_amount_out):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "amount_mode='separate' requires col_amount_in and/or col_amount_out",
        )

    profile = ImportProfile(**payload.model_dump())
    db.add(profile)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"A profile named '{payload.name}' already exists")
    db.refresh(profile)
    return profile


@router.delete("/import-profiles/{profile_id}", response_model=Message)
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    profile = _get_profile(db, profile_id)
    db.delete(profile)
    db.commit()
    return Message(detail="Profile deleted")


# ------------------------------------------------------------------ inspect


@router.post("/imports/inspect", response_model=InspectResponse)
async def inspect(file: UploadFile = File(...), max_lines: int = 40):
    """Shows the file as it really is: numbered lines, delimiter, encoding.

    Without this there is no way to guess `skip_rows` on a file with a
    preamble — some statements carry 62 rows before the real header.
    """
    data = await _read_upload(file)
    return InspectResponse(**inspect_file(data, max_lines=max_lines))


# ------------------------------------------------------------------ preview


@router.post("/imports/preview", response_model=PreviewResponse)
async def preview(
    file: UploadFile = File(...),
    profile_id: int = Form(...),
    account_id: int = Form(...),
    limit: int = Form(50),
    db: Session = Depends(get_db),
):
    """Parses the file and shows the result WITHOUT writing anything.

    It also flags which rows would be duplicates of what is already stored:
    re-importing overlapping ranges is the normal flow.
    """
    data = await _read_upload(file)
    profile = _get_profile(db, profile_id)
    account = _get_account(db, account_id)

    parse_profile = to_parse_profile(profile, default_currency=account.currency)
    result = parse_file(data, parse_profile)
    duplicates = existing_hashes(db, account.id, [r.dedup_hash for r in result.rows])

    summary = summarize(result, duplicates)
    return PreviewResponse(
        **summary,
        rows=[
            PreviewRow(
                line_no=r.line_no,
                booked_at=r.booked_at,
                amount=r.amount,
                currency=r.currency,
                description=r.description,
                counterparty=r.counterparty,
                external_id=r.external_id,
                mcc=r.mcc,
                category_hint=r.category_hint,
                is_duplicate=r.dedup_hash in duplicates,
            )
            for r in result.rows[:limit]
        ],
        errors=[PreviewError(line_no=e.line_no, message=e.message) for e in result.errors[:limit]],
    )


# ------------------------------------------------------------------- commit


@router.post("/imports/commit", response_model=ImportRunOut, status_code=status.HTTP_201_CREATED)
async def commit(
    file: UploadFile = File(...),
    profile_id: int = Form(...),
    account_id: int = Form(...),
    db: Session = Depends(get_db),
):
    data = await _read_upload(file)
    profile = _get_profile(db, profile_id)
    account = _get_account(db, account_id)

    run = commit_import(
        db,
        account=account,
        profile=profile,
        filename=file.filename or "unnamed.csv",
        data=data,
        parse_profile=to_parse_profile(profile, default_currency=account.currency),
    )
    if run.status == "failed":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, run.error_message)
    return run


@router.get("/imports", response_model=list[ImportRunOut])
def list_runs(db: Session = Depends(get_db)):
    return list(db.scalars(select(ImportRun).order_by(ImportRun.created_at.desc()).limit(100)))


@router.post("/imports/{run_id}/revert", response_model=Message)
def revert(run_id: int, db: Session = Depends(get_db)):
    run = db.get(ImportRun, run_id)
    if not run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
    if run.status == "reverted":
        raise HTTPException(status.HTTP_409_CONFLICT, "Import already undone")

    deleted = revert_import(db, run)
    return Message(
        detail=f"Import undone: {deleted} transactions removed",
        data={"run_id": run_id, "deleted": deleted},
    )
