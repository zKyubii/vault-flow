"""Schemi Pydantic per l'API."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AccountType = Literal["checking", "card", "cash", "savings"]


# ------------------------------------------------------------------ conti


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: AccountType = "checking"
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    iban: str | None = Field(default=None, max_length=34)


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    type: AccountType | None = None
    iban: str | None = Field(default=None, max_length=34)
    archived: bool | None = None


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str
    currency: str
    iban: str | None
    archived: bool


class AccountWithBalance(AccountOut):
    balance: Decimal
    transactions: int


# ------------------------------------------------------- profili di import


class ImportProfileBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    account_id: int | None = None

    delimiter: str = Field(default=",", min_length=1, max_length=4)
    encoding: str = "utf-8"
    has_header: bool = True
    skip_rows: int = Field(default=0, ge=0)

    date_format: str = "%d/%m/%Y"
    decimal_separator: str = Field(default=",", min_length=1, max_length=1)
    thousands_separator: str | None = Field(default=None, max_length=1)

    amount_mode: Literal["signed", "separate"] = "signed"
    col_date: str
    col_description: str
    col_counterparty: str | None = None
    col_amount: str | None = None
    col_amount_in: str | None = None
    col_amount_out: str | None = None
    col_external_id: str | None = None
    col_mcc: str | None = None
    # Solo per banche che tengono commissioni/imposte FUORI dall'importo.
    col_fee: str | None = None
    col_tax: str | None = None
    col_currency: str | None = None
    col_category_hint: str | None = None

    currency_symbols: str = ""
    stop_at_value: str | None = None
    skip_unparsable: bool = False
    invert_sign: bool = False


class ImportProfileCreate(ImportProfileBase):
    pass


class ImportProfileOut(ImportProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


# --------------------------------------------------------------- ispezione


class InspectLine(BaseModel):
    number: int
    text: str


class InspectResponse(BaseModel):
    """Aiuta a costruire la mappatura: mostra il file com'è davvero."""

    encoding_used: str
    delimiter_guess: str
    total_lines: int
    lines: list[InspectLine]
    header_guess: list[str] | None
    header_line_guess: int | None


# --------------------------------------------------------------- anteprima


class PreviewRow(BaseModel):
    line_no: int
    booked_at: date
    amount: Decimal
    currency: str
    description: str
    counterparty: str | None
    external_id: str | None
    mcc: str | None
    category_hint: str | None
    is_duplicate: bool


class PreviewError(BaseModel):
    line_no: int
    message: str


class PreviewResponse(BaseModel):
    rows_parsed: int
    rows_new: int
    rows_duplicate: int
    rows_failed: int
    stopped_at_line: int | None
    total_amount: Decimal
    date_from: date | None
    date_to: date | None
    rows: list[PreviewRow]
    errors: list[PreviewError]


# ------------------------------------------------------------ import runs


class ImportRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: int | None
    account_id: int
    filename: str
    rows_total: int
    rows_imported: int
    rows_skipped: int
    status: str
    error_message: str | None
    created_at: datetime


# ----------------------------------------------------------- transazioni


class TransactionCreate(BaseModel):
    account_id: int
    booked_at: date
    amount: Decimal
    description: str = Field(min_length=1, max_length=500)
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    counterparty: str | None = Field(default=None, max_length=255)
    category_id: int | None = None
    notes: str | None = None


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    import_run_id: int | None
    booked_at: date
    amount: Decimal
    currency: str
    description: str
    counterparty: str | None
    category_id: int | None
    category_source: str | None
    source: str
    mcc: str | None
    notes: str | None


class TransactionPage(BaseModel):
    total: int
    items: list[TransactionOut]


# ------------------------------------------------------------- categorie


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    parent_id: int | None
    color: str
    icon: str | None
    is_income: bool
    exclude_from_stats: bool


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parent_id: int | None = None
    color: str = Field(default="#9e9e9e", pattern=r"^#[0-9a-fA-F]{6}$")
    icon: str | None = Field(default=None, max_length=40)
    is_income: bool = False
    # per giroconti, depositi su broker, saldi iniziali: restano nei saldi
    # ma non contano come entrate o uscite
    exclude_from_stats: bool = False


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    parent_id: int | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    icon: str | None = Field(default=None, max_length=40)
    is_income: bool | None = None
    exclude_from_stats: bool | None = None


# --------------------------------------------------------------- regole


MatchType = Literal["contains", "starts_with", "exact", "regex"]
RuleField = Literal["description", "counterparty"]


class CategoryRuleCreate(BaseModel):
    pattern: str = Field(min_length=1, max_length=255)
    category_id: int
    # priorità più bassa = valutata prima = vince
    priority: int = Field(default=100, ge=0)
    field: RuleField = "description"
    match_type: MatchType = "contains"
    account_id: int | None = None
    enabled: bool = True


class CategoryRuleUpdate(BaseModel):
    pattern: str | None = Field(default=None, min_length=1, max_length=255)
    category_id: int | None = None
    priority: int | None = Field(default=None, ge=0)
    field: RuleField | None = None
    match_type: MatchType | None = None
    account_id: int | None = None
    enabled: bool | None = None


class CategoryRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pattern: str
    category_id: int
    priority: int
    field: str
    match_type: str
    account_id: int | None
    enabled: bool


class ApplyRulesRequest(BaseModel):
    account_id: int | None = None
    only_uncategorized: bool = True
    dry_run: bool = True


class ApplyRulesResponse(BaseModel):
    dry_run: bool
    examined: int
    matched: int
    updated: int
    protected: int
    by_category: dict[str, int]
    samples: list[dict[str, Any]]


class RuleFromTransaction(BaseModel):
    transaction_id: int
    category_id: int
    # se assente si deduce dalla descrizione
    pattern: str | None = Field(default=None, max_length=255)
    priority: int = Field(default=100, ge=0)
    # se True riclassifica anche i movimenti che avevano già una categoria
    # da regola (mai quelli scelti a mano)
    recategorize: bool = False


class RuleSuggestion(BaseModel):
    pattern: str
    count: int
    total: Decimal
    samples: list[str]


class SetCategoryRequest(BaseModel):
    category_id: int | None


class Message(BaseModel):
    detail: str
    data: dict[str, Any] | None = None
