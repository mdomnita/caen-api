"""Tests for scripts/refresh_company_financial_stats.py.

Uses a throwaway SQLite DB (same lazy-DATABASE_URL pattern as
tests/test_import_company_caen.py) rather than the shared Postgres fixture.
"""
from pathlib import Path

import pytest
from sqlalchemy import select

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company, CompanyFinancial, CompanyFinancialStats
from routers.company_utils import normalize_company_name
from scripts.refresh_company_financial_stats import refresh_company_financial_stats


@pytest.fixture
def stats_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "financial_stats.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    init_postgres()


def _make_company(session, *, cui: int, county: str, name: str) -> Company:
    company = Company(
        name=name,
        normalized_name=normalize_company_name(name),
        cui=cui,
        county=county,
    )
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def _stats_row(session, an, camp, judet, caen):
    return session.scalar(
        select(CompanyFinancialStats).where(
            CompanyFinancialStats.an == an,
            CompanyFinancialStats.camp == camp,
            CompanyFinancialStats.judet == judet,
            CompanyFinancialStats.caen == caen,
        )
    )


def test_computes_national_judet_and_caen_granularities(stats_db: None) -> None:
    with SessionLocal() as session:
        company_a = _make_company(session, cui=1, county="Cluj", name="A SRL")
        company_b = _make_company(session, cui=2, county="Cluj", name="B SRL")
        company_c = _make_company(session, cui=3, county="Bucuresti", name="C SRL")
        session.add_all(
            [
                CompanyFinancial(company_id=company_a.id, an=2023, sursa="MFP", caen="6201", cifra_afaceri=100),
                CompanyFinancial(company_id=company_b.id, an=2023, sursa="MFP", caen="4711", cifra_afaceri=200),
                CompanyFinancial(company_id=company_c.id, an=2023, sursa="MFP", caen="6201", cifra_afaceri=300),
            ]
        )
        session.commit()

    written = refresh_company_financial_stats(batch_size=10)
    assert written == 5  # national + 2 judete + 2 caen codes, all for cifra_afaceri/2023

    with SessionLocal() as session:
        national = _stats_row(session, 2023, "cifra_afaceri", None, None)
        assert national.numar_firme == 3
        assert national.suma == 600
        assert national.medie == pytest.approx(200.0)
        assert national.minim == 100
        assert national.maxim == 300
        assert national.mediana is None  # not computed, see module docstring

        cluj = _stats_row(session, 2023, "cifra_afaceri", "Cluj", None)
        assert cluj.numar_firme == 2
        assert cluj.suma == 300
        assert cluj.minim == 100
        assert cluj.maxim == 200

        bucuresti = _stats_row(session, 2023, "cifra_afaceri", "Bucuresti", None)
        assert bucuresti.numar_firme == 1
        assert bucuresti.suma == 300

        caen_6201 = _stats_row(session, 2023, "cifra_afaceri", None, "6201")
        assert caen_6201.numar_firme == 2
        assert caen_6201.suma == 400

        caen_4711 = _stats_row(session, 2023, "cifra_afaceri", None, "4711")
        assert caen_4711.numar_firme == 1
        assert caen_4711.suma == 200

        # The combined judet+caen granularity is intentionally not precomputed.
        assert _stats_row(session, 2023, "cifra_afaceri", "Cluj", "6201") is None


def test_ignores_null_field_values(stats_db: None) -> None:
    with SessionLocal() as session:
        company = _make_company(session, cui=1, county="Cluj", name="A SRL")
        session.add(
            CompanyFinancial(company_id=company.id, an=2023, sursa="MFP", caen="6201", cifra_afaceri=None)
        )
        session.commit()

    refresh_company_financial_stats(batch_size=10)

    with SessionLocal() as session:
        assert _stats_row(session, 2023, "cifra_afaceri", None, None) is None


def test_rerun_replaces_stale_rows_rather_than_accumulating(stats_db: None) -> None:
    with SessionLocal() as session:
        company = _make_company(session, cui=1, county="Cluj", name="A SRL")
        session.add(
            CompanyFinancial(company_id=company.id, an=2023, sursa="MFP", caen="6201", cifra_afaceri=100)
        )
        session.commit()

    refresh_company_financial_stats(batch_size=10)
    refresh_company_financial_stats(batch_size=10)  # rerun with no data change

    with SessionLocal() as session:
        national = _stats_row(session, 2023, "cifra_afaceri", None, None)
        assert national.numar_firme == 1  # not doubled by the second run
        assert national.suma == 100
