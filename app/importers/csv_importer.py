"""Parser CSV generico, guidato da un profilo di mappatura.

Nessun parser dedicato per singola banca: le banche cambiano formato e i
parser su misura si rompono. Qui la mappatura è dati (`ParseProfile`), non
codice, e l'utente la costruisce dall'interfaccia.

Questo modulo **non importa nulla del database** di proposito: è logica pura,
testabile su un file senza container, e riutilizzabile per l'anteprima prima
di scrivere qualsiasi cosa.
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
    """Come leggere un file. Rispecchia la tabella `import_profiles`."""

    col_date: str
    col_description: str

    delimiter: str = ","
    encoding: str = "utf-8"
    has_header: bool = True
    skip_rows: int = 0

    date_format: str = "%d/%m/%Y"
    decimal_separator: str = ","
    thousands_separator: str | None = None

    # 'signed'   -> una colonna con il segno
    # 'separate' -> due colonne, entrate e uscite (molte banche italiane)
    amount_mode: str = "signed"
    col_amount: str | None = None
    col_amount_in: str | None = None
    col_amount_out: str | None = None

    col_counterparty: str | None = None
    col_external_id: str | None = None
    col_mcc: str | None = None
    # Attenzione: da configurare SOLO se la banca tiene fee/tax fuori
    # dall'importo (Trade Republic sì, Revolut no — lì la commissione è
    # già dentro il movimento e sommarla conterebbe due volte).
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
# lettura grezza
# --------------------------------------------------------------------------


def _decode(data: bytes, encoding: str) -> str:
    enc = (encoding or "utf-8").strip().lower()
    # utf-8-sig toglie il BOM che Excel mette in testa ai file salvati da lui
    if enc in {"utf-8", "utf8"}:
        enc = "utf-8-sig"
    return data.decode(enc, errors="replace")


def _iter_rows(text: str, profile: ParseProfile) -> Iterator[tuple[int, dict]]:
    """Righe come dizionari. Senza intestazione le chiavi sono '0', '1', ..."""
    lines = text.splitlines(keepends=True)
    if profile.skip_rows:
        lines = lines[profile.skip_rows :]
    buf = io.StringIO("".join(lines))

    if profile.has_header:
        reader = csv.DictReader(buf, delimiter=profile.delimiter, restkey="__extra__")
        first_data_line = profile.skip_rows + 2  # 1-based, +1 per l'intestazione
        for i, row in enumerate(reader):
            yield first_data_line + i, {(k or ""): v for k, v in row.items()}
    else:
        reader = csv.reader(buf, delimiter=profile.delimiter)
        first_data_line = profile.skip_rows + 1
        for i, row in enumerate(reader):
            yield first_data_line + i, {str(j): v for j, v in enumerate(row)}


def _get(row: dict, key: str | None) -> Any:
    """Legge per nome di colonna; se non esiste, prova come indice numerico."""
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
# conversioni
# --------------------------------------------------------------------------


def parse_amount(value: Any, profile: ParseProfile) -> Decimal:
    """Interpreta un importo.

    Regge i casi visti nei file veri:
      "139.000000"   Trade Republic
      "-€19.41"      Revolut, segno PRIMA del simbolo
      "€1,035.60"    separatore delle migliaia
      "(12,34)"      notazione contabile per i negativi
      "19,41-"       segno in coda
    """
    if value is None:
        raise ValueError("importo mancante")
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

    # il segno poteva stare anche DOPO il simbolo: "€-19.41"
    if s.startswith("-"):
        negative = True
        s = s[1:]

    if profile.thousands_separator:
        s = s.replace(profile.thousands_separator, "")
    if profile.decimal_separator and profile.decimal_separator != ".":
        s = s.replace(profile.decimal_separator, ".")

    s = re.sub(r"[^0-9.]", "", s)
    if not s or not _HAS_DIGIT.search(s):
        raise ValueError(f"importo non interpretabile: {value!r}")

    try:
        amount = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"importo non interpretabile: {value!r}") from None

    return -amount if negative else amount


def parse_date(value: Any, profile: ParseProfile) -> date:
    s = str(value or "").strip()
    if not s:
        raise ValueError("data mancante")
    try:
        return datetime.strptime(s, profile.date_format).date()
    except ValueError:
        pass
    # molte sorgenti affiancano un timestamp ISO completo
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        raise ValueError(
            f"data non interpretabile: {value!r} (atteso {profile.date_format})"
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
    """Chiave di deduplica.

    Se la banca fornisce un id nativo lo usiamo: è più affidabile di qualsiasi
    hash calcolato. Altrimenti ricostruiamo una chiave dai campi, includendo
    il numero di occorrenza nel file — senza, due caffè identici lo stesso
    giorno collasserebbero in uno solo e perderesti una spesa vera.
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

    # Commissioni e imposte tenute fuori dall'importo dalla banca.
    # Caso reale Trade Republic: riga bollo con amount=0.00 e tax=-8.50.
    # Senza questo, quegli 8,50 € sparirebbero dai conti.
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
# parsing completo
# --------------------------------------------------------------------------


def parse_file(data: bytes, profile: ParseProfile) -> ParseResult:
    """Interpreta un file intero.

    Non solleva eccezioni sulle righe singole: le raccoglie in `errors`, così
    l'anteprima può mostrarle all'utente. Sta al chiamante decidere se
    procedere comunque (`skip_unparsable`) o fermarsi.
    """
    result = ParseResult()
    text = _decode(data, profile.encoding)
    occurrences: dict[tuple, int] = {}

    for line_no, raw in _iter_rows(text, profile):
        values = [str(v) for v in raw.values() if v is not None]

        # riga di chiusura sezione (Revolut termina i movimenti con "Total")
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


# Profili pronti per i formati già analizzati. Servono come esempio e come
# base per i test: in produzione i profili stanno nel database e li crea
# l'utente dall'interfaccia.
PROFILE_TRADE_REPUBLIC = ParseProfile(
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

PROFILE_REVOLUT = ParseProfile(
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
