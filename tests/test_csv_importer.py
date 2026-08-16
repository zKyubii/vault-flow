"""Test del parser CSV.

I dati sono inventati ma la **struttura** riproduce i formati reali che hanno
motivato ogni scelta: Trade Republic (id nativo, MCC, tax fuori dall'importo)
e Revolut (preambolo, riga "Total", simbolo di valuta, date in inglese).

Nessun dato bancario vero entra qui: il repo è pubblico.
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

# ---------------------------------------------------------------- importi


@pytest.fixture
def profilo_eur():
    return ParseProfile(
        col_date="d",
        col_description="x",
        decimal_separator=".",
        thousands_separator=",",
        currency_symbols="€$£",
    )


@pytest.fixture
def profilo_italiano():
    return ParseProfile(
        col_date="d",
        col_description="x",
        decimal_separator=",",
        thousands_separator=".",
        currency_symbols="€",
    )


@pytest.mark.parametrize(
    "grezzo, atteso",
    [
        ("-€19.41", "-19.41"),        # segno PRIMA del simbolo (Revolut)
        ("€1,035.60", "1035.60"),     # separatore delle migliaia
        ("-€1,157.79", "-1157.79"),
        ("€0.00", "0.00"),
        ("139.000000", "139"),        # sei decimali (Trade Republic)
        ("(12.34)", "-12.34"),        # notazione contabile
        ("19.41-", "-19.41"),         # segno in coda
        ("€-7.50", "-7.50"),          # segno DOPO il simbolo
        ("  €  2,500.00  ", "2500.00"),
        ("", "0"),                    # cella vuota = zero, non errore
    ],
)
def test_parse_amount_formati(profilo_eur, grezzo, atteso):
    assert parse_amount(grezzo, profilo_eur) == Decimal(atteso)


@pytest.mark.parametrize(
    "grezzo, atteso",
    [("1.234,56", "1234.56"), ("-€ 89,90", "-89.90"), ("0,00", "0"), ("12,5", "12.5")],
)
def test_parse_amount_virgola_decimale(profilo_italiano, grezzo, atteso):
    assert parse_amount(grezzo, profilo_italiano) == Decimal(atteso)


def test_parse_amount_non_interpretabile(profilo_eur):
    with pytest.raises(ValueError):
        parse_amount("non un numero", profilo_eur)


def test_colonne_entrate_uscite_separate():
    """Molte banche italiane usano due colonne invece del segno."""
    profilo = ParseProfile(
        col_date="d",
        col_description="x",
        amount_mode="separate",
        col_amount_in="Entrate",
        col_amount_out="Uscite",
        decimal_separator=",",
        thousands_separator=".",
    )
    assert resolve_amount({"Entrate": "1.500,00", "Uscite": ""}, profilo) == Decimal("1500.00")
    assert resolve_amount({"Entrate": "", "Uscite": "89,90"}, profilo) == Decimal("-89.90")
    # se la banca scrive già le uscite in negativo, il risultato non cambia
    assert resolve_amount({"Entrate": "", "Uscite": "-89,90"}, profilo) == Decimal("-89.90")


def test_fee_e_tax_sommate_all_importo():
    """Caso Trade Republic: il bollo sta in `tax`, non in `amount`.

    Importando solo `amount` quei soldi sparirebbero dai conti.
    """
    profilo = ParseProfile(
        col_date="d",
        col_description="x",
        decimal_separator=".",
        col_amount="amount",
        col_fee="fee",
        col_tax="tax",
    )
    bollo = {"amount": "0.000000", "fee": "", "tax": "-8.50"}
    assert resolve_amount(bollo, profilo) == Decimal("-8.50")

    interessi = {"amount": "0.560000", "fee": "", "tax": "-0.15"}
    assert resolve_amount(interessi, profilo) == Decimal("0.41")


def test_invert_sign():
    """Alcune banche esportano le spese come numeri positivi."""
    profilo = ParseProfile(
        col_date="d", col_description="x", col_amount="a",
        decimal_separator=".", invert_sign=True,
    )
    assert resolve_amount({"a": "50.00"}, profilo) == Decimal("-50.00")


# ------------------------------------------------------------------- date


def test_parse_date_formato_inglese():
    profilo = ParseProfile(col_date="d", col_description="x", date_format="%b %d, %Y")
    assert parse_date("Mar 14, 2024", profilo).isoformat() == "2024-03-14"


def test_parse_date_fallback_iso():
    """Se il formato configurato non combacia, si tenta comunque l'ISO."""
    profilo = ParseProfile(col_date="d", col_description="x", date_format="%d/%m/%Y")
    assert parse_date("2025-01-29T06:53:37.993119Z", profilo).isoformat() == "2025-01-29"


def test_parse_date_non_interpretabile():
    profilo = ParseProfile(col_date="d", col_description="x", date_format="%d/%m/%Y")
    with pytest.raises(ValueError):
        parse_date("marzo scorso", profilo)


# -------------------------------------------------------------- deduplica


def test_dedup_id_nativo_batte_hash_calcolato():
    """Con un id della banca l'hash dipende solo da quello."""
    a = compute_dedup_hash(external_id="abc-123", booked_at=None, amount=None)
    b = compute_dedup_hash(external_id="abc-123", description="descrizione diversa")
    assert a == b


def test_dedup_righe_identiche_stesso_giorno():
    """Due caffè uguali lo stesso giorno devono restare DUE transazioni."""
    from datetime import date

    comuni = dict(booked_at=date(2025, 11, 17), amount=Decimal("-28.33"), description="Trattoria Da Gino")
    primo = compute_dedup_hash(**comuni, occurrence=0)
    secondo = compute_dedup_hash(**comuni, occurrence=1)
    assert primo != secondo


def test_dedup_stabile_tra_due_letture():
    """Reimportando lo stesso file gli hash devono coincidere, così il
    vincolo UNIQUE scarta i duplicati."""
    from datetime import date

    args = dict(
        booked_at=date(2025, 11, 17),
        amount=Decimal("-28.33"),
        description="  TRATTORIA   da  gino ",  # spaziatura e maiuscole irrilevanti
        occurrence=0,
    )
    assert compute_dedup_hash(**args) == compute_dedup_hash(
        booked_at=date(2025, 11, 17),
        amount=Decimal("-28.33"),
        description="Trattoria Da Gino",
        occurrence=0,
    )


# --------------------------------------------------------- file completi


CSV_TRADE_REPUBLIC = (
    b'"date","amount","fee","tax","currency","description","transaction_id","counterparty_name","mcc_code"\n'
    b'"2025-02-02","-139.000000","","","EUR","Card Transaction","tx-001","","6012"\n'
    b'"2025-06-01","0.560000","","-0.15","EUR","Interest payment","tx-002","",""\n'
    b'"2026-07-09","0.000000","","-8.50","EUR","Stamp Duty Tax","tx-003","",""\n'
)


def test_file_stile_trade_republic():
    profilo = ParseProfile(
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
    res = parse_file(CSV_TRADE_REPUBLIC, profilo)

    assert res.errors == []
    assert len(res.rows) == 3
    assert res.rows[0].amount == Decimal("-139.00")
    assert res.rows[0].mcc == "6012"
    assert res.rows[0].external_id == "tx-001"
    assert res.rows[1].amount == Decimal("0.41")   # 0.56 + (-0.15)
    assert res.rows[2].amount == Decimal("-8.50")  # 0.00 + (-8.50)
    assert sum(r.amount for r in res.rows) == Decimal("-147.09")


CSV_REVOLUT = (
    b'"Intestazione di riepilogo",,,\n'
    b'"Altra riga di preambolo",,,\n'
    b'Date,Description,Category,"Money in/out",Balance,,,\n'
    b'"Mar 14, 2024","Top-up by *1234","Top up","\xe2\x82\xac19.41","\xe2\x82\xac19.41",,,\n'
    b'"Nov 17, 2025","Trattoria Da Gino",Merchant,"-\xe2\x82\xac28.33","\xe2\x82\xac1,000.00",,,\n'
    b'"Nov 17, 2025","Trattoria Da Gino",Merchant,"-\xe2\x82\xac28.33","\xe2\x82\xac971.67",,,\n'
    b'"Jul 7, 2026","Grosso acquisto",Merchant,"-\xe2\x82\xac1,157.79","\xe2\x82\xac0.00",,,\n'
    b'Total,,,"\xe2\x82\xac0.00",,,\n'
    b'"Sezione successiva con altre colonne",,,\n'
)


def test_file_stile_revolut():
    profilo = ParseProfile(
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
    res = parse_file(CSV_REVOLUT, profilo)

    assert res.errors == []
    assert len(res.rows) == 4, "la riga Total e la sezione successiva vanno escluse"
    assert res.stopped_at is not None
    assert res.rows[0].booked_at.isoformat() == "2024-03-14"
    assert res.rows[0].amount == Decimal("19.41")
    assert res.rows[0].category_hint == "Top up"
    assert res.rows[3].amount == Decimal("-1157.79")

    # le due righe identiche restano due transazioni distinte
    tex = [r for r in res.rows if r.description == "Trattoria Da Gino"]
    assert len(tex) == 2
    assert tex[0].dedup_hash != tex[1].dedup_hash


def test_righe_illeggibili_finiscono_in_errors_non_in_eccezione():
    profilo = ParseProfile(
        col_date="Date", col_description="Desc", col_amount="Amt",
        date_format="%Y-%m-%d", decimal_separator=".",
    )
    dati = (
        b"Date,Desc,Amt\n"
        b"2025-01-01,Buona,10.00\n"
        b"data-sbagliata,Rotta,10.00\n"
        b"2025-01-03,Importo rotto,non-un-numero\n"
    )
    res = parse_file(dati, profilo)

    assert len(res.rows) == 1
    assert len(res.errors) == 2
    assert res.errors[0].line_no == 3
