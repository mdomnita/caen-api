"""Tests for scripts/import_company_fiscal_info.py.

Same throwaway-SQLite pattern as tests/test_import_company_caen.py.
"""
from pathlib import Path

import pytest
from sqlalchemy import select

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company, CompanyFiscalInfo
from routers.company_utils import normalize_company_name
from scripts.import_company_fiscal_info import (
    _parse_date,
    _parse_datetime,
    _row_to_payload,
    import_company_fiscal_info,
)


@pytest.fixture
def fiscal_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "fiscal.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    init_postgres()


def _make_company(session, *, cui: int, name: str = "Test SRL") -> Company:
    company = Company(
        name=name,
        normalized_name=normalize_company_name(name),
        cui=cui,
    )
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


_HEADER = (
    "COD_FISCAL^DENUMIRE^COD_FISCAL_PARINTE^TIP_UNITATE^TIP_CONTRIB^LOCALITATE^STRADA^NR^"
    "DATA_INREGISTRARE^DATA_PRELUCRARE^FAX^SECTOR^TELEFON^JUDET_COMERT^NR_COMERT^AN_COMERT^"
    "ACT_AUTORIZARE^TVA^DATA_RADIERE^COD_POSTAL^DATA_STARE^STARE^JUDET^IMP100^IMP120^"
    "DETALII_ADRESA^BLOC^SCARA^ETAJ^AP"
)


def _write_csv(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "date_identificare_platitori_a.csv"
    content = _HEADER + "\n" + "\n".join(rows) + "\n"
    path.write_text(content, encoding="cp1250")
    return path


class TestParseDate:
    def test_dot_format(self) -> None:
        assert _parse_date("15.05.2025").isoformat() == "2025-05-15"

    def test_empty_is_none(self) -> None:
        assert _parse_date("") is None
        assert _parse_date(None) is None

    def test_slash_format_not_matched(self) -> None:
        # sursa foloseste punct, nu slash -- confirma ca nu se potriveste accidental cu alt format
        assert _parse_date("15/05/2025") is None


class TestParseDatetime:
    def test_with_time(self) -> None:
        dt = _parse_datetime("21.05.2025 17:30:25")
        assert dt.isoformat() == "2025-05-21T17:30:25+00:00"

    def test_date_only_fallback(self) -> None:
        dt = _parse_datetime("21.05.2025")
        assert dt.date().isoformat() == "2025-05-21"


class TestRowToPayload:
    def test_filters_out_non_pj(self) -> None:
        row = {"COD_FISCAL": "19", "TIP_CONTRIB": "PF", "TIP_UNITATE": "Sediu central"}
        assert _row_to_payload(row) is None

    def test_filters_out_non_sediu_central(self) -> None:
        row = {"COD_FISCAL": "19", "TIP_CONTRIB": "PJ", "TIP_UNITATE": "Sucursala"}
        assert _row_to_payload(row) is None

    def test_invalid_cui_returns_none(self) -> None:
        row = {"COD_FISCAL": "", "TIP_CONTRIB": "PJ", "TIP_UNITATE": "Sediu central"}
        assert _row_to_payload(row) is None

    def test_builds_indicatori_raw_from_unmapped_columns(self) -> None:
        row = {
            "COD_FISCAL": "19", "TIP_CONTRIB": "PJ", "TIP_UNITATE": "Sediu central",
            "TVA": "DA", "IMP100": "DA", "IMP120": "NU ",
        }
        payload = _row_to_payload(row)
        assert payload["indicatori_fiscali_raw"] == "IMP100=DA;IMP120=NU"

    def test_tva_none_when_blank(self) -> None:
        row = {"COD_FISCAL": "19", "TIP_CONTRIB": "PJ", "TIP_UNITATE": "Sediu central", "TVA": ""}
        assert _row_to_payload(row)["tva_platitor"] is None

    def test_combines_address_details(self) -> None:
        row = {
            "COD_FISCAL": "19", "TIP_CONTRIB": "PJ", "TIP_UNITATE": "Sediu central",
            "DETALII_ADRESA": "corp B", "BLOC": "4", "SCARA": "A", "ETAJ": "2", "AP": "10",
        }
        payload = _row_to_payload(row)
        assert payload["adresa_fiscala_detalii"] == "corp B, Bloc 4, Scara A, Etaj 2, Ap. 10"


class TestImportCompanyFiscalInfo:
    def test_imports_matching_company_by_cui(self, fiscal_db, tmp_path: Path) -> None:
        with SessionLocal() as session:
            company = _make_company(session, cui=19)

        csv_path = _write_csv(
            tmp_path,
            [
                "19^BUCUR OBOR SA^^Sediu central^PJ^Bucureşti^Sos. COLENTINA^2^15.05.2025^"
                "21.05.2025 17:30:25^0212528371^2^252593435^^^^^DA^^^23.02.2006^INREGISTRAT^"
                "MUNICIPIUL BUCUREŞTI^DA^NU^^^^^"
            ],
        )

        stats = import_company_fiscal_info(csv_path)

        assert stats.inserted == 1
        assert stats.skipped_no_company == 0
        with SessionLocal() as session:
            info = session.scalar(select(CompanyFiscalInfo).where(CompanyFiscalInfo.company_id == company.id))
            assert info.tva_platitor is True
            assert info.data_inregistrare_fiscala.isoformat() == "2025-05-15"
            assert info.stare_fiscala == "INREGISTRAT"
            assert info.adresa_fiscala_judet == "MUNICIPIUL BUCUREŞTI"

    def test_skips_row_with_unknown_cui(self, fiscal_db, tmp_path: Path) -> None:
        csv_path = _write_csv(
            tmp_path,
            [
                "999999^NECUNOSCUTA^^Sediu central^PJ^Bucureşti^Str^1^15.05.2025^"
                "21.05.2025 17:30:25^^2^^^^^^DA^^^^INREGISTRAT^BUCURESTI^DA^NU^^^^^"
            ],
        )
        stats = import_company_fiscal_info(csv_path)
        assert stats.inserted == 0
        assert stats.skipped_no_company == 1

    def test_rerun_updates_instead_of_duplicating(self, fiscal_db, tmp_path: Path) -> None:
        with SessionLocal() as session:
            company = _make_company(session, cui=19)

        csv_path = _write_csv(
            tmp_path,
            [
                "19^X^^Sediu central^PJ^Bucureşti^Str^1^15.05.2025^21.05.2025 17:30:25^^2^^^^^^DA^^^^INREGISTRAT^BUCURESTI^DA^NU^^^^^"
            ],
        )
        import_company_fiscal_info(csv_path)
        stats2 = import_company_fiscal_info(csv_path)

        assert stats2.updated == 1
        assert stats2.inserted == 0
        with SessionLocal() as session:
            rows = list(session.scalars(select(CompanyFiscalInfo).where(CompanyFiscalInfo.company_id == company.id)))
            assert len(rows) == 1
