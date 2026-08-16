"""SQLAlchemy models.

Note: the source of truth for the schema is the files in `db/migrations/`.
These models exist to query the database and must be kept in sync by hand
whenever a migration is added.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.mysql import JSON, TINYINT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    type: Mapped[str] = mapped_column(String(20), default="checking")
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    iban: Mapped[str | None] = mapped_column(String(34), nullable=True)
    archived: Mapped[bool] = mapped_column(TINYINT(1), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # passive_deletes: the cascade delete is done by the database
    # (ON DELETE CASCADE). Without it SQLAlchemy tries to empty the foreign
    # key with UPDATE transactions SET account_id=NULL, which is NOT NULL and
    # makes deleting an account fail.
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    color: Mapped[str] = mapped_column(String(7), default="#9e9e9e")
    icon: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_income: Mapped[bool] = mapped_column(TINYINT(1), default=0)
    # Transfers between your own accounts and opening balances move money but
    # are not spending: they stay in the balances, out of the statistics.
    exclude_from_stats: Mapped[bool] = mapped_column(TINYINT(1), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    children: Mapped[list["Category"]] = relationship()


class CategoryRule(Base):
    __tablename__ = "category_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    field: Mapped[str] = mapped_column(String(20), default="description")
    match_type: Mapped[str] = mapped_column(String(20), default="contains")
    pattern: Mapped[str] = mapped_column(String(255))
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE")
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(TINYINT(1), default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ImportProfile(Base):
    __tablename__ = "import_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    delimiter: Mapped[str] = mapped_column("delimiter", String(4), default=",")
    encoding: Mapped[str] = mapped_column(String(20), default="utf-8")
    has_header: Mapped[bool] = mapped_column(TINYINT(1), default=1)
    skip_rows: Mapped[int] = mapped_column(Integer, default=0)
    date_format: Mapped[str] = mapped_column(String(32), default="%d/%m/%Y")
    decimal_separator: Mapped[str] = mapped_column(String(1), default=",")
    thousands_separator: Mapped[str | None] = mapped_column(String(1), nullable=True)
    amount_mode: Mapped[str] = mapped_column(String(10), default="signed")
    col_date: Mapped[str] = mapped_column(String(64))
    col_description: Mapped[str] = mapped_column(String(64))
    col_counterparty: Mapped[str | None] = mapped_column(String(64), nullable=True)
    col_amount: Mapped[str | None] = mapped_column(String(64), nullable=True)
    col_amount_in: Mapped[str | None] = mapped_column(String(64), nullable=True)
    col_amount_out: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # --- 003: optional columns that emerged from real CSV exports ---
    col_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    col_mcc: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Some banks keep fees and taxes OUTSIDE the amount:
    # effective amount = amount + fee + tax
    col_fee: Mapped[str | None] = mapped_column(String(64), nullable=True)
    col_tax: Mapped[str | None] = mapped_column(String(64), nullable=True)
    col_currency: Mapped[str | None] = mapped_column(String(64), nullable=True)
    col_category_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # characters to strip before parsing an amount (e.g. "€")
    currency_symbols: Mapped[str] = mapped_column(String(16), default="")
    # some statements close the transaction section with a "Total" row
    stop_at_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    skip_unparsable: Mapped[bool] = mapped_column(TINYINT(1), default=0)
    invert_sign: Mapped[bool] = mapped_column(TINYINT(1), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ImportRun(Base):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_profiles.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE")
    )
    filename: Mapped[str] = mapped_column(String(255))
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_imported: Mapped[int] = mapped_column(Integer, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE")
    )
    import_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_runs.id", ondelete="SET NULL"), nullable=True
    )
    booked_at: Mapped[date] = mapped_column(Date)
    value_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # negative = money out, positive = money in
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    description: Mapped[str] = mapped_column(String(500))
    counterparty: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    category_source: Mapped[str | None] = mapped_column(String(10), nullable=True)
    source: Mapped[str] = mapped_column(String(10))
    dedup_hash: Mapped[str] = mapped_column(String(64))
    # the bank's own id when the export provides one: in that case
    # dedup_hash = sha256(external_id)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mcc: Mapped[str | None] = mapped_column(String(4), nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    account: Mapped["Account"] = relationship(back_populates="transactions")
    category: Mapped["Category | None"] = relationship()


class Setting(Base):
    __tablename__ = "settings"

    setting_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
