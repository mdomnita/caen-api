"""
Company API tests use their own isolated PostgreSQL-URL override (via a
module-scoped fixture) instead of the shared `client` fixture in conftest.py,
since these tests need a throwaway `companies` database rather than the
suite-wide SQLite one. `DATABASE_URL` is only read lazily (inside
`company_database._get_engine()`), so — unlike `SQLITE_DB` in conftest.py —
it does not need to be set before `main` is imported; it's patched for the
duration of this module's fixture and restored afterwards.
"""
from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from main import app
from routers.company_database import SessionLocal
from routers.company_models import Company
from routers.company_utils import normalize_company_name


def _seed_companies() -> None:
    with SessionLocal() as session:
        session.add_all(
            [
                Company(
                    name="MAPIFUL S.R.L.",
                    normalized_name=normalize_company_name("MAPIFUL S.R.L."),
                    cui=12345784,
                    registration_number="J12/124/1994",
                    county="Cluj",
                    locality="Cluj-Napoca",
                    legal_form="SRL",
                ),
                Company(
                    name="MAP CONSULTING SRL",
                    normalized_name=normalize_company_name("MAP CONSULTING SRL"),
                    cui=12345678,
                    registration_number="J40/1000/2019",
                    county="Bucuresti",
                    locality="Bucuresti",
                    legal_form="SRL",
                ),
            ]
        )
        session.commit()


@pytest.fixture(scope="module")
def company_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    db_path = tmp_path_factory.mktemp("company-db") / "companies.db"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    try:
        with TestClient(app) as client:
            _seed_companies()
            yield client
    finally:
        monkeypatch.undo()


def test_search_returns_companies_by_name(company_client: TestClient) -> None:
    response = company_client.get("/companii/search", params={"q": "map"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["results"][0]["name"].startswith("MAP")


def test_company_lookup_by_cui(company_client: TestClient) -> None:
    response = company_client.get("/companii/12345784")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "MAPIFUL S.R.L."
    assert payload["county"] == "Cluj"


def test_autocomplete_limits_payload(company_client: TestClient) -> None:
    response = company_client.get("/companii/autocomplete", params={"q": "map", "limit": 1})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["results"]) == 1
    assert set(payload["results"][0]) == {"name", "cui"}
