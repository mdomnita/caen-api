from datetime import date

from scripts.import_companies import _dedupe_batch_by_cui
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