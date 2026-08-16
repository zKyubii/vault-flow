"""Logica di import: dal profilo salvato alle transazioni sul database."""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.importers.csv_importer import ParseProfile, ParseResult, parse_file
from app.models import Account, ImportProfile, ImportRun, Transaction

# Ordine dei tentativi di decodifica quando l'utente non sa che encoding ha.
# cp1252 prima di latin-1: è quello che esce da Excel su Windows.
ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


def to_parse_profile(profile: ImportProfile, default_currency: str = "EUR") -> ParseProfile:
    """Converte il profilo salvato nel database nella sua versione "pura".

    Il parser non conosce SQLAlchemy: questa è l'unica cucitura fra i due.
    """
    return ParseProfile(
        col_date=profile.col_date,
        col_description=profile.col_description,
        delimiter=profile.delimiter,
        encoding=profile.encoding,
        has_header=bool(profile.has_header),
        skip_rows=profile.skip_rows,
        date_format=profile.date_format,
        decimal_separator=profile.decimal_separator,
        thousands_separator=profile.thousands_separator,
        amount_mode=profile.amount_mode,
        col_amount=profile.col_amount,
        col_amount_in=profile.col_amount_in,
        col_amount_out=profile.col_amount_out,
        col_counterparty=profile.col_counterparty,
        col_external_id=profile.col_external_id,
        col_mcc=profile.col_mcc,
        col_fee=profile.col_fee,
        col_tax=profile.col_tax,
        col_currency=profile.col_currency,
        col_category_hint=profile.col_category_hint,
        currency_symbols=profile.currency_symbols or "",
        stop_at_value=profile.stop_at_value,
        skip_unparsable=bool(profile.skip_unparsable),
        invert_sign=bool(profile.invert_sign),
        default_currency=default_currency,
    )


def existing_hashes(db: Session, account_id: int, hashes: list[str]) -> set[str]:
    """Quali di questi hash sono già nel database per questo conto."""
    if not hashes:
        return set()
    found: set[str] = set()
    # a blocchi: un IN con migliaia di elementi è una cattiva idea
    for start in range(0, len(hashes), 500):
        chunk = hashes[start : start + 500]
        rows = db.execute(
            select(Transaction.dedup_hash).where(
                Transaction.account_id == account_id,
                Transaction.dedup_hash.in_(chunk),
            )
        )
        found.update(r[0] for r in rows)
    return found


def inspect_file(data: bytes, max_lines: int = 40) -> dict:
    """Mostra il file com'è, per aiutare a costruire la mappatura.

    Serve soprattutto ai file con preambolo (Revolut ne ha 62 righe prima
    dell'intestazione vera): senza vedere le righe numerate è impossibile
    indovinare `skip_rows`.
    """
    text = None
    encoding_used = "utf-8"
    for candidate in ENCODING_CANDIDATES:
        try:
            text = data.decode(candidate)
            encoding_used = candidate
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", errors="replace")
        encoding_used = "utf-8 (con sostituzioni)"

    all_lines = text.splitlines()
    sample = all_lines[:max_lines]

    delimiter = ","
    try:
        dialect = csv.Sniffer().sniff("\n".join(sample[:20]), delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        pass

    # L'intestazione è la prima riga con più campi non vuoti: nei file con
    # preambolo le righe di riepilogo hanno quasi tutte le celle vuote.
    header_guess = None
    header_line_guess = None
    best_score = 1
    for index, line in enumerate(sample):
        try:
            fields = next(csv.reader(io.StringIO(line), delimiter=delimiter))
        except (csv.Error, StopIteration):
            continue
        score = sum(1 for f in fields if f.strip())
        if score > best_score:
            best_score = score
            header_guess = [f.strip() for f in fields]
            header_line_guess = index + 1

    return {
        "encoding_used": encoding_used,
        "delimiter_guess": delimiter,
        "total_lines": len(all_lines),
        "lines": [{"number": i + 1, "text": t} for i, t in enumerate(sample)],
        "header_guess": header_guess,
        "header_line_guess": header_line_guess,
    }


def summarize(result: ParseResult, duplicates: set[str]) -> dict:
    rows = result.rows
    total = sum((r.amount for r in rows), Decimal(0))
    dates = [r.booked_at for r in rows]
    new_count = sum(1 for r in rows if r.dedup_hash not in duplicates)
    return {
        "rows_parsed": len(rows),
        "rows_new": new_count,
        "rows_duplicate": len(rows) - new_count,
        "rows_failed": len(result.errors),
        "stopped_at_line": result.stopped_at,
        "total_amount": total,
        "date_from": min(dates) if dates else None,
        "date_to": max(dates) if dates else None,
    }


def commit_import(
    db: Session,
    *,
    account: Account,
    profile: ImportProfile | None,
    filename: str,
    data: bytes,
    parse_profile: ParseProfile,
) -> ImportRun:
    """Scrive le transazioni nuove e registra l'operazione.

    Le righe già presenti vengono saltate (non è un errore: reimportare
    intervalli sovrapposti è il flusso normale). Il vincolo
    UNIQUE(account_id, dedup_hash) resta come rete di sicurezza.
    """
    result = parse_file(data, parse_profile)

    if result.errors and not parse_profile.skip_unparsable:
        run = ImportRun(
            profile_id=profile.id if profile else None,
            account_id=account.id,
            filename=filename,
            rows_total=result.total_seen,
            rows_imported=0,
            rows_skipped=0,
            status="failed",
            error_message=(
                f"{len(result.errors)} righe non interpretabili. "
                f"Prima: riga {result.errors[0].line_no} — {result.errors[0].message}"
            ),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    run = ImportRun(
        profile_id=profile.id if profile else None,
        account_id=account.id,
        filename=filename,
        rows_total=result.total_seen,
        status="pending",
    )
    db.add(run)
    db.flush()  # serve l'id per legarci le transazioni

    already = existing_hashes(db, account.id, [r.dedup_hash for r in result.rows])

    imported = 0
    for row in result.rows:
        if row.dedup_hash in already:
            continue
        db.add(
            Transaction(
                account_id=account.id,
                import_run_id=run.id,
                booked_at=row.booked_at,
                amount=row.amount,
                currency=row.currency,
                description=row.description or "(senza descrizione)",
                counterparty=row.counterparty,
                source="csv",
                dedup_hash=row.dedup_hash,
                external_id=row.external_id,
                mcc=row.mcc,
                raw=row.raw,
            )
        )
        already.add(row.dedup_hash)  # duplicati interni allo stesso file
        imported += 1

    run.rows_imported = imported
    run.rows_skipped = len(result.rows) - imported
    run.status = "completed"
    if result.errors:
        run.error_message = f"{len(result.errors)} righe saltate perché illeggibili"

    db.commit()
    db.refresh(run)
    return run


def revert_import(db: Session, run: ImportRun) -> int:
    """Annulla un import: cancella solo le transazioni che ha creato lui."""
    deleted = (
        db.query(Transaction).filter(Transaction.import_run_id == run.id).delete(
            synchronize_session=False
        )
    )
    run.status = "reverted"
    run.rows_imported = 0
    db.commit()
    return deleted
