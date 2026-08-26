"""Tests for scripts/update_company_stare.py.

Same throwaway-SQLite pattern as tests/test_import_company_caen.py.
"""
from pathlib import Path

import pytest

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company
from routers.company_utils import normalize_company_name
from scripts.update_company_stare import (
    firma_activa,
    update_company_stare,
)


@pytest.fixture
def stare_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "stare.db"
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


def _write_csv(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "od_stare_firma.csv"
    content = "COD_INMATRICULARE^COD\n" + "\n".join(rows) + "\n"
    path.write_text(content, encoding="utf-8")
    return path


class TestFirmaActiva:
    def test_only_functiune_is_active(self) -> None:
        assert firma_activa(["1048"]) is True

    def test_radiere_overrides_functiune(self) -> None:
        assert firma_activa(["1048", "1084"]) is False

    def test_order_does_not_matter(self) -> None:
        assert firma_activa(["1084", "1048"]) is False

    def test_neutral_code_alone_is_not_active(self) -> None:
        # 1053 = schimbare sediu -- nu e in niciuna din liste, dar fara 1048 nu e activa
        assert firma_activa(["1053"]) is False

    def test_neutral_code_with_functiune_is_active(self) -> None:
        assert firma_activa(["1048", "1053"]) is True

    def test_empty_set_is_not_active(self) -> None:
        assert firma_activa([]) is False


class TestUpdateCompanyStare:
    def test_marks_active_and_inactive_companies(self, stare_db, tmp_path: Path) -> None:
        with SessionLocal() as session:
            activa = _make_company(session, cui=1, registration_number="J1/1/2020", name="Activa SRL")
            radiata = _make_company(session, cui=2, registration_number="J1/2/2020", name="Radiata SRL")
            necunoscuta_cod = _make_company(session, cui=3, registration_number="J1/3/2020", name="Fara Cod SRL")

        csv_path = _write_csv(
            tmp_path,
            [
                "J1/1/2020^1048",
                "J1/2/2020^1048",
                "J1/2/2020^1084",  # radiata dupa ce a fost activa -- trebuie sa castige NOT_OPERATING
                "J1/3/2020^1053",  # cod neutru, fara 1048 -> inactiva
                "J9/9/2020^1048",  # nicio firma cu acest nr. de inmatriculare -- skipped_no_company
            ],
        )

        stats = update_company_stare(csv_path, batch_size=2, dry_run=False)

        assert stats.skipped_no_company == 1
        assert stats.companies_active == 1
        assert stats.companies_inactive == 2

        with SessionLocal() as session:
            activa_refreshed = session.get(Company, activa.id)
            radiata_refreshed = session.get(Company, radiata.id)
            necunoscuta_cod_refreshed = session.get(Company, necunoscuta_cod.id)
            assert activa_refreshed.is_active is True
            assert activa_refreshed.stare_verificata_la is not None
            assert radiata_refreshed.is_active is False
            assert necunoscuta_cod_refreshed.is_active is False

    def test_dry_run_does_not_write(self, stare_db, tmp_path: Path) -> None:
        with SessionLocal() as session:
            company = _make_company(session, cui=1, registration_number="J1/1/2020")

        csv_path = _write_csv(tmp_path, ["J1/1/2020^1048"])
        update_company_stare(csv_path, dry_run=True)

        with SessionLocal() as session:
            refreshed = session.get(Company, company.id)
            assert refreshed.is_active is None
            assert refreshed.stare_verificata_la is None
