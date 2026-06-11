from datetime import date
from pathlib import Path

from scripts.import_companies import ImportStats, _dedupe_batch_by_cui, _prepare_batch
from routers.company_utils import clean_text, normalize_company_name, parse_cui, parse_ro_date


def test_normalize_company_name_removes_diacritics_and_punctuation() -> None:
    assert normalize_company_name(" Școala Română S.R.L. ") == "scoala romana s r l"


def test_parse_ro_date_accepts_dd_mm_yyyy() -> None:
    assert parse_ro_date("17/11/2021") == date(2021, 11, 17)


def test_parse_ro_date_accepts_datetime_suffix() -> None:
    assert parse_ro_date("17/11/2021 09:00:20") == date(2021, 11, 17)


def test_parse_ro_date_invalid_value_becomes_none() -> None:
    assert parse_ro_date("not-a-date") is None


def test_parse_cui_keeps_only_digits() -> None:
    assert parse_cui("RO 45239009") == 45239009


def test_empty_text_becomes_none() -> None:
    assert clean_text("   ") is None


def test_dedupe_batch_by_cui_keeps_last_row_for_duplicate_cui() -> None:
    batch = [
        {"cui": 123, "name": "First"},
        {"cui": 456, "name": "Other"},
        {"cui": 123, "name": "Latest"},
    ]

    deduped = _dedupe_batch_by_cui(batch)

    assert deduped == [
        {"cui": 123, "name": "Latest"},
        {"cui": 456, "name": "Other"},
    ]


def test_row_to_payload_skips_zero_cui() -> None:
    from scripts.import_companies import _row_to_payload

    row = {
        "DENUMIRE": "Example SRL",
        "CUI": "0",
        "COD_INMATRICULARE": "J00/1/2000",
        "DATA_INMATRICULARE": "17/11/2021 09:00:20",
        "EUID": "ROONRC.J00/1/2000",
        "FORMA_JURIDICA": "SRL",
        "ADR_TARA": "Romania",
        "ADR_JUDET": "Cluj",
        "ADR_LOCALITATE": "Cluj-Napoca",
        "ADR_DEN_STRADA": "Str. Exemplu",
        "ADR_NR_STRADA": "1",
        "ADR_BLOC": "",
        "ADR_SCARA": "",
        "ADR_ETAJ": "",
        "ADR_APARTAMENT": "",
        "ADR_COD_POSTAL": "400000",
        "ADR_SECTOR": "",
        "ADR_COMPLETARE": "",
        "WEB": "",
        "TARA_FIRMA_MAMA": "",
    }

    assert _row_to_payload(row) is None


def test_prepare_batch_tracks_duplicates_and_errors() -> None:
    rows = [
        {
            "DENUMIRE": "Valid One SRL",
            "CUI": "123",
            "COD_INMATRICULARE": "J00/1/2000",
            "DATA_INMATRICULARE": "17/11/2021",
            "EUID": "ROONRC.J00/1/2000",
            "FORMA_JURIDICA": "SRL",
            "ADR_TARA": "Romania",
            "ADR_JUDET": "Cluj",
            "ADR_LOCALITATE": "Cluj-Napoca",
            "ADR_DEN_STRADA": "Str. Exemplu",
            "ADR_NR_STRADA": "1",
            "ADR_BLOC": "",
            "ADR_SCARA": "",
            "ADR_ETAJ": "",
            "ADR_APARTAMENT": "",
            "ADR_COD_POSTAL": "400000",
            "ADR_SECTOR": "",
            "ADR_COMPLETARE": "",
            "WEB": "",
            "TARA_FIRMA_MAMA": "",
        },
        {
            "DENUMIRE": "Valid Two SRL",
            "CUI": "123",
            "COD_INMATRICULARE": "J00/2/2000",
            "DATA_INMATRICULARE": "17/11/2021",
            "EUID": "ROONRC.J00/2/2000",
            "FORMA_JURIDICA": "SRL",
            "ADR_TARA": "Romania",
            "ADR_JUDET": "Cluj",
            "ADR_LOCALITATE": "Cluj-Napoca",
            "ADR_DEN_STRADA": "Str. Exemplu",
            "ADR_NR_STRADA": "2",
            "ADR_BLOC": "",
            "ADR_SCARA": "",
            "ADR_ETAJ": "",
            "ADR_APARTAMENT": "",
            "ADR_COD_POSTAL": "400000",
            "ADR_SECTOR": "",
            "ADR_COMPLETARE": "",
            "WEB": "",
            "TARA_FIRMA_MAMA": "",
        },
        {
            "DENUMIRE": "Invalid SRL",
            "CUI": "0",
            "COD_INMATRICULARE": "J00/3/2000",
            "DATA_INMATRICULARE": "17/11/2021",
            "EUID": "ROONRC.J00/3/2000",
            "FORMA_JURIDICA": "SRL",
            "ADR_TARA": "Romania",
            "ADR_JUDET": "Cluj",
            "ADR_LOCALITATE": "Cluj-Napoca",
            "ADR_DEN_STRADA": "Str. Exemplu",
            "ADR_NR_STRADA": "3",
            "ADR_BLOC": "",
            "ADR_SCARA": "",
            "ADR_ETAJ": "",
            "ADR_APARTAMENT": "",
            "ADR_COD_POSTAL": "400000",
            "ADR_SECTOR": "",
            "ADR_COMPLETARE": "",
            "WEB": "",
            "TARA_FIRMA_MAMA": "",
        },
    ]

    batch, stats = _prepare_batch(rows)

    assert len(batch) == 1
    assert batch[0]["name"] == "Valid Two SRL"
    assert stats == ImportStats(rows_seen=3, inserted=0, updated=0, duplicates=1, errors=1)


def test_import_stats_processed_property() -> None:
    stats = ImportStats(inserted=5, updated=2)

    assert stats.processed == 7


def test_read_batches_keeps_literal_quotes_in_company_name(tmp_path: Path) -> None:
    from scripts.import_companies import _read_batches

    csv_path = tmp_path / "companies.csv"
    csv_path.write_text(
        "DENUMIRE^CUI^COD_INMATRICULARE^DATA_INMATRICULARE^EUID^FORMA_JURIDICA^ADR_TARA^ADR_JUDET^ADR_LOCALITATE^ADR_DEN_STRADA^ADR_NR_STRADA^ADR_BLOC^ADR_SCARA^ADR_ETAJ^ADR_APARTAMENT^ADR_COD_POSTAL^ADR_SECTOR^ADR_COMPLETARE^WEB^TARA_FIRMA_MAMA\n"
        'CRISTEA R. DUMITRU "DEPANAREA SI INTRETINEREA APARATURII RADIO-TV." PF^123^F40/22/1991^13/02/1991^ROONRC.F40/22/1991^PF^Romania^Bucuresti^Bucuresti^Ceahlau^16^^^^^29862^6^^^\n'
        "VOTUM BUSINESS CONSULTING S.R.L.^456^J40/19358/2021^08/11/2021^ROONRC.J40/19358/2021^SRL^Romania^Bucuresti^Bucuresti Sectorul 3^Mosilor^88^^F^^F1^30152^3^^^\n",
        encoding="utf-8",
    )

    batches = list(_read_batches(csv_path, batch_size=100))

    assert len(batches) == 1
    batch, stats = batches[0]
    assert len(batch) == 2
    assert stats.errors == 0
    assert stats.duplicates == 0
    assert batch[0]["name"] == 'CRISTEA R. DUMITRU "DEPANAREA SI INTRETINEREA APARATURII RADIO-TV." PF'