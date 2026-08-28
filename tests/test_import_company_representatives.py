"""Tests for scripts/import_company_representatives.py.

Same throwaway-SQLite pattern as tests/test_import_company_caen.py.
"""
from pathlib import Path

import pytest
from sqlalchemy import select

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company, CompanyRepresentative
from routers.company_utils import normalize_company_name
from scripts.import_company_representatives import import_company_representatives


@pytest.fixture
def reps_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "reps.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    init_postgres()


def _make_company(session, *, cui: int, registration_number: str, name: str = "Test SRL") -> Company:
    company = Company(
        name=name,
        normalized_name=normalize_company_name(name),
        cui=cui,
        registration_number=registration_number,
    )
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


_HEADER = (
    "COD_INMATRICULARE^PERSOANA_IMPUTERNICITA^CALITATE^DATA_NASTERE^LOCALITATE_NASTERE^"
    "JUDET_NASTERE^TARA_NASTERE^LOCALITATE^JUDET^TARA"
)


def _write_csv(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "od_reprezentanti_legali.csv"
    content = _HEADER + "\n" + "\n".join(rows) + "\n"
    path.write_text(content, encoding="utf-8-sig")
    return path


class TestImportCompanyRepresentatives:
    def test_imports_representative_matched_by_registration_number(self, reps_db, tmp_path: Path) -> None:
        with SessionLocal() as session:
            company = _make_company(session, cui=1, registration_number="J40/1/2020")

        csv_path = _write_csv(
            tmp_path,
            ["J40/1/2020^GRUITA MARIA^administrator^30/06/1970^Cluj^Cluj^Romania^Cluj-Napoca^Cluj^Romania"],
        )

        stats = import_company_representatives(csv_path)

        assert stats.inserted == 1
        assert stats.skipped_no_company == 0
        with SessionLocal() as session:
            rep = session.scalar(select(CompanyRepresentative).where(CompanyRepresentative.company_id == company.id))
            assert rep.nume == "GRUITA MARIA"
            assert rep.calitate == "administrator"
            assert rep.data_nasterii.isoformat() == "1970-06-30"
            assert rep.localitate == "Cluj-Napoca"

    def test_multiple_representatives_per_company(self, reps_db, tmp_path: Path) -> None:
        with SessionLocal() as session:
            company = _make_company(session, cui=1, registration_number="J40/1/2020")

        csv_path = _write_csv(
            tmp_path,
            [
                "J40/1/2020^ADMIN UNU^administrator^^^^^^^",
                "J40/1/2020^ADMIN DOI^administrator^^^^^^^",
            ],
        )
        stats = import_company_representatives(csv_path)

        assert stats.inserted == 2
        with SessionLocal() as session:
            names = {
                rep.nume
                for rep in session.scalars(
                    select(CompanyRepresentative).where(CompanyRepresentative.company_id == company.id)
                )
            }
            assert names == {"ADMIN UNU", "ADMIN DOI"}

    def test_skips_unknown_registration_number(self, reps_db, tmp_path: Path) -> None:
        csv_path = _write_csv(tmp_path, ["J99/999/2099^NIMENI^administrator^^^^^^^"])
        stats = import_company_representatives(csv_path)
        assert stats.inserted == 0
        assert stats.skipped_no_company == 1

    def test_row_without_name_is_skipped_as_invalid(self, reps_db, tmp_path: Path) -> None:
        csv_path = _write_csv(tmp_path, ["J40/1/2020^^administrator^^^^^^^"])
        stats = import_company_representatives(csv_path)
        assert stats.inserted == 0
        assert stats.errors == 1

    def test_rerun_updates_instead_of_duplicating(self, reps_db, tmp_path: Path) -> None:
        with SessionLocal() as session:
            company = _make_company(session, cui=1, registration_number="J40/1/2020")

        csv_path = _write_csv(
            tmp_path, ["J40/1/2020^GRUITA MARIA^administrator^30/06/1970^^^^Cluj^Cluj^Romania"]
        )
        import_company_representatives(csv_path)
        stats2 = import_company_representatives(csv_path)

        assert stats2.updated == 1
        assert stats2.inserted == 0
        with SessionLocal() as session:
            rows = list(
                session.scalars(
                    select(CompanyRepresentative).where(CompanyRepresentative.company_id == company.id)
                )
            )
            assert len(rows) == 1
