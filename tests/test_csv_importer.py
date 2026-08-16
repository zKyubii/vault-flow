"""Tests for the CSV parser.

The data is invented, but the **shape** reproduces the real formats that
motivated every choice: an export with native ids, MCC codes and tax kept
outside the amount, and a multi-section statement with a preamble, a "Total"
row, a currency symbol and English dates.

No real banking data belongs here: the repository is public.
"""

from decimal import Decimal

import pytest

from app.importers.csv_importer import (
    ParseProfile,
    compute_dedup_hash,
    parse_amount,
    parse_date,
    parse_file,
    resolve_amount,
)

# ---------------------------------------------------------------- amounts


@pytest.fixture
def eur_profile():
    return ParseProfile(
        col_date="d",
        col_description="x",
        decimal_separator=".",
        thousands_separator=",",
        currency_symbols="€$£",
    )


@pytest.fixture
def italian_profile():
    return ParseProfile(
        col_date="d",
        col_description="x",
        decimal_separator=",",
        thousands_separator=".",
        currency_symbols="€",
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("-€19.41", "-19.41"),        # sign BEFORE the symbol
        ("€1,035.60", "1035.60"),     # thousands separator
        ("-€1,157.79", "-1157.79"),
        ("€0.00", "0.00"),
        ("139.000000", "139"),        # six decimal places
        ("(12.34)", "-12.34"),        # accounting notation
        ("19.41-", "-19.41"),         # trailing sign
        ("€-7.50", "-7.50"),          # sign AFTER the symbol
        ("  €  2,500.00  ", "2500.00"),
        ("", "0"),                    # empty cell = zero, not an error
    ],
)
def test_amount_formats(eur_profile, raw, expected):
    assert parse_amount(raw, eur_profile) == Decimal(expected)


@pytest.mark.parametrize(
    "raw, expected",
    [("1.234,56", "1234.56"), ("-€ 89,90", "-89.90"), ("0,00", "0"), ("12,5", "12.5")],
)
def test_comma_decimal_separator(italian_profile, raw, expected):
    assert parse_amount(raw, italian_profile) == Decimal(expected)


def test_unparsable_amount(eur_profile):
    with pytest.raises(ValueError):
        parse_amount("not a number", eur_profile)


def test_separate_debit_and_credit_columns():
    """Many banks use two columns instead of a sign."""
    profile = ParseProfile(
        col_date="d",
        col_description="x",
        amount_mode="separate",
        col_amount_in="In",
        col_amount_out="Out",
        decimal_separator=",",
        thousands_separator=".",
    )
    assert resolve_amount({"In": "1.500,00", "Out": ""}, profile) == Decimal("1500.00")
    assert resolve_amount({"In": "", "Out": "89,90"}, profile) == Decimal("-89.90")
    # if the bank already writes outgoing amounts as negative, nothing changes
    assert resolve_amount({"In": "", "Out": "-89,90"}, profile) == Decimal("-89.90")


def test_fee_and_tax_are_added_to_the_amount():
    """Some banks keep stamp duty in `tax`, not in `amount`.

    Importing only `amount` would make that money vanish from the books.
    """
    profile = ParseProfile(
        col_date="d",
        col_description="x",
        decimal_separator=".",
        col_amount="amount",
        col_fee="fee",
        col_tax="tax",
    )
    stamp_duty = {"amount": "0.000000", "fee": "", "tax": "-8.50"}
    assert resolve_amount(stamp_duty, profile) == Decimal("-8.50")

    interest = {"amount": "0.560000", "fee": "", "tax": "-0.15"}
    assert resolve_amount(interest, profile) == Decimal("0.41")


def test_invert_sign():
    """Some banks export expenses as positive numbers."""
    profile = ParseProfile(
        col_date="d", col_description="x", col_amount="a",
        decimal_separator=".", invert_sign=True,
    )
    assert resolve_amount({"a": "50.00"}, profile) == Decimal("-50.00")


# ------------------------------------------------------------------ dates


def test_english_date_format():
    profile = ParseProfile(col_date="d", col_description="x", date_format="%b %d, %Y")
    assert parse_date("Mar 14, 2024", profile).isoformat() == "2024-03-14"


def test_iso_fallback():
    """If the configured format does not match, ISO is still attempted."""
    profile = ParseProfile(col_date="d", col_description="x", date_format="%d/%m/%Y")
    assert parse_date("2025-01-29T06:53:37.993119Z", profile).isoformat() == "2025-01-29"


def test_unparsable_date():
    profile = ParseProfile(col_date="d", col_description="x", date_format="%d/%m/%Y")
    with pytest.raises(ValueError):
        parse_date("last March", profile)


# ------------------------------------------------------------ de-duplication


def test_a_native_id_beats_the_computed_hash():
    """With an id from the bank, the hash depends on nothing else."""
    a = compute_dedup_hash(external_id="abc-123", booked_at=None, amount=None)
    b = compute_dedup_hash(external_id="abc-123", description="a different description")
    assert a == b


def test_identical_rows_on_the_same_day_stay_separate():
    """Two identical coffees on the same day must remain TWO transactions."""
    from datetime import date

    common = dict(booked_at=date(2025, 11, 17), amount=Decimal("-28.33"), description="Trattoria Da Gino")
    first = compute_dedup_hash(**common, occurrence=0)
    second = compute_dedup_hash(**common, occurrence=1)
    assert first != second


def test_the_hash_is_stable_across_two_reads():
    """Re-importing the same file must produce the same hashes, so the UNIQUE
    constraint discards the duplicates."""
    from datetime import date

    args = dict(
        booked_at=date(2025, 11, 17),
        amount=Decimal("-28.33"),
        description="  TRATTORIA   da  gino ",  # spacing and case are irrelevant
        occurrence=0,
    )
    assert compute_dedup_hash(**args) == compute_dedup_hash(
        booked_at=date(2025, 11, 17),
        amount=Decimal("-28.33"),
        description="Trattoria Da Gino",
        occurrence=0,
    )


# --------------------------------------------------------------- whole files


CSV_WITH_NATIVE_IDS = (
    b'"date","amount","fee","tax","currency","description","transaction_id","counterparty_name","mcc_code"\n'
    b'"2025-02-02","-139.000000","","","EUR","Card Transaction","tx-001","","6012"\n'
    b'"2025-06-01","0.560000","","-0.15","EUR","Interest payment","tx-002","",""\n'
    b'"2026-07-09","0.000000","","-8.50","EUR","Stamp Duty Tax","tx-003","",""\n'
)


def test_file_with_native_ids_and_tax_outside_the_amount():
    profile = ParseProfile(
        col_date="date",
        col_description="description",
        date_format="%Y-%m-%d",
        decimal_separator=".",
        col_amount="amount",
        col_fee="fee",
        col_tax="tax",
        col_currency="currency",
        col_external_id="transaction_id",
        col_mcc="mcc_code",
    )
    result = parse_file(CSV_WITH_NATIVE_IDS, profile)

    assert result.errors == []
    assert len(result.rows) == 3
    assert result.rows[0].amount == Decimal("-139.00")
    assert result.rows[0].mcc == "6012"
    assert result.rows[0].external_id == "tx-001"
    assert result.rows[1].amount == Decimal("0.41")   # 0.56 + (-0.15)
    assert result.rows[2].amount == Decimal("-8.50")  # 0.00 + (-8.50)
    assert sum(r.amount for r in result.rows) == Decimal("-147.09")


CSV_MULTI_SECTION = (
    b'"Summary heading",,,\n'
    b'"Another preamble row",,,\n'
    b'Date,Description,Category,"Money in/out",Balance,,,\n'
    b'"Mar 14, 2024","Top-up by *1234","Top up","\xe2\x82\xac19.41","\xe2\x82\xac19.41",,,\n'
    b'"Nov 17, 2025","Trattoria Da Gino",Merchant,"-\xe2\x82\xac28.33","\xe2\x82\xac1,000.00",,,\n'
    b'"Nov 17, 2025","Trattoria Da Gino",Merchant,"-\xe2\x82\xac28.33","\xe2\x82\xac971.67",,,\n'
    b'"Jul 7, 2026","Large purchase",Merchant,"-\xe2\x82\xac1,157.79","\xe2\x82\xac0.00",,,\n'
    b'Total,,,"\xe2\x82\xac0.00",,,\n'
    b'"Next section with different columns",,,\n'
)


def test_multi_section_statement():
    profile = ParseProfile(
        col_date="Date",
        col_description="Description",
        skip_rows=2,
        date_format="%b %d, %Y",
        decimal_separator=".",
        thousands_separator=",",
        col_amount="Money in/out",
        col_category_hint="Category",
        currency_symbols="€",
        stop_at_value="Total",
    )
    result = parse_file(CSV_MULTI_SECTION, profile)

    assert result.errors == []
    assert len(result.rows) == 4, "the Total row and the next section must be excluded"
    assert result.stopped_at is not None
    assert result.rows[0].booked_at.isoformat() == "2024-03-14"
    assert result.rows[0].amount == Decimal("19.41")
    assert result.rows[0].category_hint == "Top up"
    assert result.rows[3].amount == Decimal("-1157.79")

    # the two identical rows remain two distinct transactions
    same = [r for r in result.rows if r.description == "Trattoria Da Gino"]
    assert len(same) == 2
    assert same[0].dedup_hash != same[1].dedup_hash


def test_unreadable_rows_land_in_errors_instead_of_raising():
    profile = ParseProfile(
        col_date="Date", col_description="Desc", col_amount="Amt",
        date_format="%Y-%m-%d", decimal_separator=".",
    )
    data = (
        b"Date,Desc,Amt\n"
        b"2025-01-01,Good,10.00\n"
        b"bad-date,Broken,10.00\n"
        b"2025-01-03,Broken amount,not-a-number\n"
    )
    result = parse_file(data, profile)

    assert len(result.rows) == 1
    assert len(result.errors) == 2
    assert result.errors[0].line_no == 3
