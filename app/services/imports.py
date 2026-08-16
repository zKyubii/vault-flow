"""Import logic: from the saved profile to rows in the database."""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.importers.csv_importer import ParseProfile, ParseResult, parse_file
from app.models import Account, ImportProfile, ImportRun, Transaction

# Order in which we try to decode when the user does not know the encoding.
# cp1252 before latin-1: that is what Excel on Windows produces.
ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


def to_parse_profile(profile: ImportProfile, default_currency: str = "EUR") -> ParseProfile:
    """Converts the profile stored in the database into its "pure" form.

    The parser knows nothing about SQLAlchemy: this is the only seam between
    the two.
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
    """Which of these hashes are already in the database for this account."""
    if not hashes:
        return set()
    found: set[str] = set()
    # in chunks: an IN with thousands of elements is a bad idea
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
    """Shows the file as it really is, to help build the mapping.

    It matters most for files with a preamble (some statements have 62 rows
    before the real header): without seeing numbered lines there is no way to
    guess `skip_rows`.
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
        encoding_used = "utf-8 (with replacements)"

    all_lines = text.splitlines()
    sample = all_lines[:max_lines]

    delimiter = ","
    try:
        dialect = csv.Sniffer().sniff("\n".join(sample[:20]), delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        pass

    # The header is the first row with the most non-empty fields: in files
    # with a preamble the summary rows have nearly every cell empty.
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
    """Writes the new transactions and records the operation.

    Rows already present are skipped (not an error: re-importing overlapping
    ranges is the normal flow). The UNIQUE(account_id, dedup_hash) constraint
    remains as a safety net.
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
                f"{len(result.errors)} rows could not be parsed. "
                f"First: line {result.errors[0].line_no} — {result.errors[0].message}"
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
    db.flush()  # we need the id to attach the transactions to it

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
                description=row.description or "(no description)",
                counterparty=row.counterparty,
                source="csv",
                dedup_hash=row.dedup_hash,
                external_id=row.external_id,
                mcc=row.mcc,
                raw=row.raw,
            )
        )
        already.add(row.dedup_hash)  # duplicates within the same file
        imported += 1

    run.rows_imported = imported
    run.rows_skipped = len(result.rows) - imported
    run.status = "completed"
    if result.errors:
        run.error_message = f"{len(result.errors)} rows skipped because they were unreadable"

    db.commit()
    db.refresh(run)
    return run


def revert_import(db: Session, run: ImportRun) -> int:
    """Undoes an import: deletes only the transactions it created."""
    deleted = (
        db.query(Transaction).filter(Transaction.import_run_id == run.id).delete(
            synchronize_session=False
        )
    )
    run.status = "reverted"
    run.rows_imported = 0
    db.commit()
    return deleted
