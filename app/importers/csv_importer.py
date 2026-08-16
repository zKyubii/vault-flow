"""Generic CSV parser, driven by a mapping profile.

No per-bank parsers: banks change their formats and bespoke parsers break.
Here the mapping is data (`ParseProfile`), not code, and the user builds it
from the interface.

This module deliberately **imports nothing from the database layer**: it is
pure logic, testable against a file without a container, and reusable for the
preview before anything is written.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterator

CENTS = Decimal("0.01")
_HAS_DIGIT = re.compile(r"\d")


@dataclass(slots=True)
class ParseProfile:
    """How to read a file. Mirrors the `import_profiles` table."""

    col_date: str
    col_description: str

    delimiter: str = ","
    encoding: str = "utf-8"
    has_header: bool = True
    skip_rows: int = 0

    date_format: str = "%d/%m/%Y"
    decimal_separator: str = ","
    thousands_separator: str | None = None

    # 'signed'   -> a single column carrying the sign
    # 'separate' -> two columns, money in and money out (common in Italy)
    amount_mode: str = "signed"
    col_amount: str | None = None
    col_amount_in: str | None = None
    col_amount_out: str | None = None

    col_counterparty: str | None = None
    col_external_id: str | None = None
    col_mcc: str | None = None
    # Careful: only configure these if the bank keeps fees and taxes OUTSIDE
    # the amount. Some do; others already fold the fee into the transaction,
    # and adding it would count it twice.
    col_fee: str | None = None
    col_tax: str | None = None
    col_currency: str | None = None
    col_category_hint: str | None = None

    currency_symbols: str = ""
    stop_at_value: str | None = None
    skip_unparsable: bool = False
    invert_sign: bool = False
    default_currency: str = "EUR"


@dataclass(slots=True)
class ParsedRow:
    line_no: int
    booked_at: date
    amount: Decimal
    currency: str
    description: str
    counterparty: str | None
    external_id: str | None
    mcc: str | None
    category_hint: str | None
    dedup_hash: str
    raw: dict[str, Any]


@dataclass(slots=True)
class RowError:
    line_no: int
    message: str
    raw: dict[str, Any]


@dataclass(slots=True)
class ParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    stopped_at: int | None = None

    @property
    def total_seen(self) -> int:
        return len(self.rows) + len(self.errors)


# --------------------------------------------------------------------------
# raw reading
# --------------------------------------------------------------------------


def _decode(data: bytes, encoding: str) -> str:
    enc = (encoding or "utf-8").strip().lower()
    # utf-8-sig strips the BOM that Excel puts at the start of files it saves
    if enc in {"utf-8", "utf8"}:
        enc = "utf-8-sig"
    return data.decode(enc, errors="replace")


def _iter_rows(text: str, profile: ParseProfile) -> Iterator[tuple[int, dict]]:
    """Rows as dictionaries. Without a header the keys are '0', '1', ..."""
    lines = text.splitlines(keepends=True)
    if profile.skip_rows:
        lines = lines[profile.skip_rows :]
    buf = io.StringIO("".join(lines))

    if profile.has_header:
        reader = csv.DictReader(buf, delimiter=profile.delimiter, restkey="__extra__")
        first_data_line = profile.skip_rows + 2  # 1-based, +1 for the header
        for i, row in enumerate(reader):
            yield first_data_line + i, {(k or ""): v for k, v in row.items()}
    else:
        reader = csv.reader(buf, delimiter=profile.delimiter)
        first_data_line = profile.skip_rows + 1
        for i, row in enumerate(reader):
            yield first_data_line + i, {str(j): v for j, v in enumerate(row)}


def _get(row: dict, key: str | None) -> Any:
    """Reads by column name; if that fails, tries the key as an index."""
    if key is None or key == "":
        return None
    if key in row:
        return row[key]
    try:
        idx = int(key)
    except (TypeError, ValueError):
        return None
    values = list(row.values())
    return values[idx] if 0 <= idx < len(values) else None


# --------------------------------------------------------------------------
# conversions
# --------------------------------------------------------------------------


def parse_amount(value: Any, profile: ParseProfile) -> Decimal:
    """Parses an amount.

    Handles the cases seen in real files:
      "139.000000"   six decimal places
      "-€19.41"      sign BEFORE the currency symbol
      "€1,035.60"    thousands separator
      "(12,34)"      accounting notation for negatives
      "19,41-"       trailing sign
    """
    if value is None:
        raise ValueError("missing amount")
    s = str(value).strip()
    if not s:
        return Decimal("0")

    negative = False

    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
    if s.endswith("-"):
        negative = True
        s = s[:-1].strip()
    if s.startswith("-"):
        negative = True
        s = s[1:]
    elif s.startswith("+"):
        s = s[1:]

    for ch in profile.currency_symbols:
        s = s.replace(ch, "")
    s = s.replace("\xa0", "").replace(" ", "")

    # the sign could also sit AFTER the symbol: "€-19.41"
    if s.startswith("-"):
        negative = True
        s = s[1:]

    if profile.thousands_separator:
        s = s.replace(profile.thousands_separator, "")
    if profile.decimal_separator and profile.decimal_separator != ".":
        s = s.replace(profile.decimal_separator, ".")

    s = re.sub(r"[^0-9.]", "", s)
    if not s or not _HAS_DIGIT.search(s):
        raise ValueError(f"cannot parse amount: {value!r}")

    try:
        amount = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"cannot parse amount: {value!r}") from None

    return -amount if negative else amount


def parse_date(value: Any, profile: ParseProfile) -> date:
    s = str(value or "").strip()
    if not s:
        raise ValueError("missing date")
    try:
        return datetime.strptime(s, profile.date_format).date()
    except ValueError:
        pass
    # many sources also carry a full ISO timestamp
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        raise ValueError(
            f"cannot parse date: {value!r} (expected {profile.date_format})"
        ) from None


def normalize_description(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def compute_dedup_hash(
    *,
    external_id: str | None = None,
    booked_at: date | None = None,
    amount: Decimal | None = None,
    description: str | None = None,
    occurrence: int = 0,
) -> str:
    """De-duplication key.

    If the bank provides a native id we use it: it is more reliable than any
    computed hash. Otherwise we rebuild a key from the fields, including the
    occurrence number within the file — without it, two identical coffees on
    the same day would collapse into one and you would lose a real expense.
    """
    if external_id:
        payload = f"ext:{external_id}"
    else:
        payload = (
            f"{booked_at.isoformat() if booked_at else ''}"
            f"|{amount}|{normalize_description(description)}|{occurrence}"
        )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_amount(row: dict, profile: ParseProfile) -> Decimal:
    if profile.amount_mode == "separate":
        raw_in = _get(row, profile.col_amount_in)
        raw_out = _get(row, profile.col_amount_out)
        value_in = parse_amount(raw_in, profile) if str(raw_in or "").strip() else Decimal(0)
        value_out = parse_amount(raw_out, profile) if str(raw_out or "").strip() else Decimal(0)
        amount = abs(value_in) - abs(value_out)
    else:
        amount = parse_amount(_get(row, profile.col_amount), profile)

    if profile.invert_sign:
        amount = -amount

    # Fees and taxes some banks keep outside the amount. Real case: a stamp
    # duty row with amount=0.00 and tax=-8.50. Without this, those 8.50 would
    # vanish from the books.
    for column in (profile.col_fee, profile.col_tax):
        if not column:
            continue
        raw_value = _get(row, column)
        if str(raw_value or "").strip():
            amount += parse_amount(raw_value, profile)

    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def _clean_mcc(value: Any) -> str | None:
    s = re.sub(r"\D", "", str(value or ""))
    return s.zfill(4) if 1 <= len(s) <= 4 else None


# --------------------------------------------------------------------------
# full parse
# --------------------------------------------------------------------------


def parse_file(data: bytes, profile: ParseProfile) -> ParseResult:
    """Parses a whole file.

    It does not raise on individual rows: it collects them in `errors`, so the
    preview can show them to the user. It is up to the caller to decide
    whether to proceed anyway (`skip_unparsable`) or stop.
    """
    result = ParseResult()
    text = _decode(data, profile.encoding)
    occurrences: dict[tuple, int] = {}

    for line_no, raw in _iter_rows(text, profile):
        values = [str(v) for v in raw.values() if v is not None]

        # section-closing row (some statements end the transactions with
        # a "Total" line)
        if profile.stop_at_value:
            first = values[0].strip() if values else ""
            if first == profile.stop_at_value:
                result.stopped_at = line_no
                break

        if not any(v.strip() for v in values):
            continue

        try:
            booked_at = parse_date(_get(raw, profile.col_date), profile)
            amount = resolve_amount(raw, profile)
            description = str(_get(raw, profile.col_description) or "").strip()

            external_id = str(_get(raw, profile.col_external_id) or "").strip() or None
            key = (booked_at, amount, normalize_description(description))
            occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrence + 1

            counterparty = str(_get(raw, profile.col_counterparty) or "").strip() or None
            currency = (
                str(_get(raw, profile.col_currency) or "").strip()
                or profile.default_currency
            ).upper()[:3]
            hint = str(_get(raw, profile.col_category_hint) or "").strip() or None

            result.rows.append(
                ParsedRow(
                    line_no=line_no,
                    booked_at=booked_at,
                    amount=amount,
                    currency=currency,
                    description=description,
                    counterparty=counterparty,
                    external_id=external_id,
                    mcc=_clean_mcc(_get(raw, profile.col_mcc)),
                    category_hint=hint,
                    dedup_hash=compute_dedup_hash(
                        external_id=external_id,
                        booked_at=booked_at,
                        amount=amount,
                        description=description,
                        occurrence=occurrence,
                    ),
                    raw={k: v for k, v in raw.items() if k != "__extra__"},
                )
            )
        except ValueError as exc:
            result.errors.append(RowError(line_no, str(exc), raw))

    return result


# Ready-made profiles for the formats analysed so far. They serve as examples
# and as a basis for tests: in production the profiles live in the database
# and the user creates them from the interface.
PROFILE_NATIVE_IDS = ParseProfile(
    col_date="date",
    col_description="description",
    date_format="%Y-%m-%d",
    decimal_separator=".",
    amount_mode="signed",
    col_amount="amount",
    col_counterparty="counterparty_name",
    col_external_id="transaction_id",
    col_mcc="mcc_code",
    col_fee="fee",
    col_tax="tax",
    col_currency="currency",
    col_category_hint="type",
)

PROFILE_MULTI_SECTION = ParseProfile(
    col_date="Date",
    col_description="Description",
    skip_rows=62,
    date_format="%b %d, %Y",
    decimal_separator=".",
    thousands_separator=",",
    amount_mode="signed",
    col_amount="Money in/out",
    col_category_hint="Category",
    currency_symbols="€$£",
    stop_at_value="Total",
    skip_unparsable=True,
)
