import os
import tempfile

from starlette.testclient import TestClient


_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_tmp.name}"

from routers.company_database import SessionLocal, init_postgres  # noqa: E402
from routers.company_main import app  # noqa: E402
from routers.company_models import Company  # noqa: E402
from routers.company_utils import normalize_company_name  # noqa: E402


def _seed_companies() -> None:
    init_postgres()
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


_seed_companies()
client = TestClient(app)


def test_search_returns_companies_by_name() -> None:
    response = client.get("/search", params={"q": "map"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["results"][0]["name"].startswith("MAP")


def test_company_lookup_by_cui() -> None:
    response = client.get("/companies/12345784")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "MAPIFUL S.R.L."
    assert payload["county"] == "Cluj"


def test_autocomplete_limits_payload() -> None:
    response = client.get("/autocomplete", params={"q": "map", "limit": 1})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["results"]) == 1
    assert set(payload["results"][0]) == {"name", "cui"}