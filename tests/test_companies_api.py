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
import requests
from sqlalchemy import select
from starlette.testclient import TestClient

from main import app
from routers.company_database import SessionLocal
from routers.company_models import Company, CompanyFinancial
from routers.company_utils import normalize_company_name
# NOU: pentru testele GET /companii/{cui}/coordonate
from services.geocoding import GeocodeResult, get_geocoding_provider


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
                # NOU: firma deja geocodificata de scripts/geocode_companies.py
                Company(
                    name="GEOCODATA STOCATA SRL",
                    normalized_name=normalize_company_name("GEOCODATA STOCATA SRL"),
                    cui=99900001,
                    county="Cluj",
                    locality="Cluj-Napoca",
                    street="Strada Exemplu",
                    street_number="10",
                    legal_form="SRL",
                    latitude=46.7712,
                    longitude=23.6236,
                    geocode_score=95.5,
                    geocode_status="ok",
                ),
                # NOU: firma cu adresa valida dar inca negeocodificata (calea live)
                Company(
                    name="GEOCODARE LIVE SRL",
                    normalized_name=normalize_company_name("GEOCODARE LIVE SRL"),
                    cui=99900002,
                    county="Cluj",
                    locality="Cluj-Napoca",
                    street="Strada Donath",
                    street_number="20",
                    legal_form="SRL",
                ),
                # NOU: latitude/longitude populate dar geocode_status != 'ok' -- verifica ca
                # decizia "stocat vs. live" se bazeaza doar pe coloanele lat/lon, nu pe status.
                Company(
                    name="LAT LON FARA STATUS OK SRL",
                    normalized_name=normalize_company_name("LAT LON FARA STATUS OK SRL"),
                    cui=99900004,
                    county="Cluj",
                    locality="Cluj-Napoca",
                    street="Strada Exemplu",
                    street_number="30",
                    legal_form="SRL",
                    latitude=46.75,
                    longitude=23.6,
                    geocode_score=80.0,
                    geocode_status="error",
                ),
                # NOU: firma fara nicio informatie de adresa (adresa insuficienta)
                Company(
                    name="FARA ADRESA SRL",
                    normalized_name=normalize_company_name("FARA ADRESA SRL"),
                    cui=99900003,
                    legal_form="SRL",
                ),
                # NOU: firme dedicate testelor GET /companii/financiar/clasament -- CUI-uri
                # distincte de restul fixture-ului ca sa nu afecteze celelalte teste.
                Company(
                    name="TOP FIRMA UNU SRL",
                    normalized_name=normalize_company_name("TOP FIRMA UNU SRL"),
                    cui=99900011,
                    county="Cluj",
                    legal_form="SRL",
                ),
                Company(
                    name="TOP FIRMA DOI SRL",
                    normalized_name=normalize_company_name("TOP FIRMA DOI SRL"),
                    cui=99900012,
                    county="Bucuresti",
                    legal_form="SRL",
                ),
                Company(
                    name="TOP FIRMA TREI SRL",
                    normalized_name=normalize_company_name("TOP FIRMA TREI SRL"),
                    cui=99900013,
                    county="Cluj",
                    legal_form="SRL",
                ),
            ]
        )
        session.flush()

        # NOU: date financiare (tabela company_financials) pentru testele GET /companii/{cui}/financiar
        mapiful = session.scalar(select(Company).where(Company.cui == 12345784))
        session.add_all(
            [
                CompanyFinancial(
                    company_id=mapiful.id,
                    an=2022,
                    sursa="MFP",
                    caen="6201",
                    cifra_afaceri=100_000,
                    profit_net=10_000,
                    numar_salariati=5,
                ),
                CompanyFinancial(
                    company_id=mapiful.id,
                    an=2023,
                    sursa="MFP",
                    caen="6201",
                    cifra_afaceri=150_000,
                    profit_net=20_000,
                    numar_salariati=6,
                ),
            ]
        )

        # NOU: date financiare pentru testele GET /companii/financiar/clasament
        top_unu = session.scalar(select(Company).where(Company.cui == 99900011))
        top_doi = session.scalar(select(Company).where(Company.cui == 99900012))
        top_trei = session.scalar(select(Company).where(Company.cui == 99900013))
        session.add_all(
            [
                CompanyFinancial(company_id=top_unu.id, an=2023, sursa="MFP", caen="6201", cifra_afaceri=500_000),
                CompanyFinancial(company_id=top_doi.id, an=2023, sursa="MFP", caen="4711", cifra_afaceri=300_000),
                CompanyFinancial(company_id=top_trei.id, an=2023, sursa="MFP", caen="6201", cifra_afaceri=700_000),
            ]
        )
        session.commit()


# NOU: dublura de test pentru GeocodingProvider, folosita pentru
# GET /companii/{cui}/coordonate fara a apela ArcGIS cu adevarat.
class _FakeGeocodingProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.result: GeocodeResult | None = None
        self.raise_exc: Exception | None = None

    def geocode(self, address: str) -> GeocodeResult | None:
        self.calls.append(address)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.result


@pytest.fixture
def fake_geocoding_provider() -> Iterator[_FakeGeocodingProvider]:
    provider = _FakeGeocodingProvider()
    app.dependency_overrides[get_geocoding_provider] = lambda: provider
    try:
        yield provider
    finally:
        app.dependency_overrides.pop(get_geocoding_provider, None)


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


# NOU: latitude/longitude pe GET /companii/{cui}
def test_company_out_includes_null_lat_lon_when_not_geocoded(company_client: TestClient) -> None:
    response = company_client.get("/companii/12345784")
    assert response.status_code == 200
    payload = response.json()
    assert payload["latitude"] is None
    assert payload["longitude"] is None


def test_company_out_includes_stored_lat_lon(company_client: TestClient) -> None:
    response = company_client.get("/companii/99900001")
    assert response.status_code == 200
    payload = response.json()
    assert payload["latitude"] == pytest.approx(46.7712)
    assert payload["longitude"] == pytest.approx(23.6236)


def test_autocomplete_limits_payload(company_client: TestClient) -> None:
    response = company_client.get("/companii/autocomplete", params={"q": "map", "limit": 1})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["results"]) == 1
    assert set(payload["results"][0]) == {"name", "cui"}


# NOU: teste pentru GET /companii/{cui}/coordonate
class TestCompanyCoordonate:
    def test_unknown_cui_returns_404(self, company_client: TestClient, fake_geocoding_provider) -> None:
        response = company_client.get("/companii/00000001/coordonate")
        assert response.status_code == 404
        assert fake_geocoding_provider.calls == []

    def test_stored_coordinates_returned_without_provider_call(
        self, company_client: TestClient, fake_geocoding_provider
    ) -> None:
        response = company_client.get("/companii/99900001/coordonate")
        assert response.status_code == 200
        payload = response.json()
        assert payload["cui"] == 99900001
        assert payload["latitude"] == pytest.approx(46.7712)
        assert payload["longitude"] == pytest.approx(23.6236)
        assert payload["score"] == pytest.approx(95.5)
        assert payload["sursa"] == "stocat"
        assert fake_geocoding_provider.calls == []  # no live ArcGIS call for an already-stored company

    def test_insufficient_address_returns_404_without_provider_call(
        self, company_client: TestClient, fake_geocoding_provider
    ) -> None:
        response = company_client.get("/companii/99900003/coordonate")
        assert response.status_code == 404
        assert fake_geocoding_provider.calls == []

    # NOU: decizia stocat-vs-live se bazeaza doar pe latitude/longitude, nu pe geocode_status.
    def test_stored_coordinates_used_even_when_geocode_status_not_ok(
        self, company_client: TestClient, fake_geocoding_provider
    ) -> None:
        response = company_client.get("/companii/99900004/coordonate")
        assert response.status_code == 200
        payload = response.json()
        assert payload["sursa"] == "stocat"
        assert payload["latitude"] == pytest.approx(46.75)
        assert payload["longitude"] == pytest.approx(23.6)
        assert fake_geocoding_provider.calls == []

    def test_live_geocode_success(self, company_client: TestClient, fake_geocoding_provider) -> None:
        fake_geocoding_provider.result = GeocodeResult(
            lat=46.77, lon=23.59, score=97.2, formatted_address="Strada Donath 20, Cluj-Napoca", provider="arcgis",
        )
        response = company_client.get("/companii/99900002/coordonate")
        assert response.status_code == 200
        payload = response.json()
        assert payload["latitude"] == pytest.approx(46.77)
        assert payload["longitude"] == pytest.approx(23.59)
        assert payload["score"] == pytest.approx(97.2)
        assert payload["sursa"] == "live"
        assert "Strada Donath" in payload["adresa_folosita"]
        assert len(fake_geocoding_provider.calls) == 1

    def test_live_geocode_does_not_persist_to_db(self, company_client: TestClient, fake_geocoding_provider) -> None:
        # Read-only guarantee: a live geocode must never write companies.latitude/longitude.
        fake_geocoding_provider.result = GeocodeResult(
            lat=46.77, lon=23.59, score=97.2, formatted_address="Strada Donath 20, Cluj-Napoca", provider="arcgis",
        )
        company_client.get("/companii/99900002/coordonate")
        with SessionLocal() as session:
            company = session.scalar(select(Company).where(Company.cui == 99900002))
            assert company.latitude is None
            assert company.longitude is None
            assert company.geocode_status is None

    def test_live_geocode_no_match_returns_404(self, company_client: TestClient, fake_geocoding_provider) -> None:
        fake_geocoding_provider.result = None
        response = company_client.get("/companii/99900002/coordonate")
        assert response.status_code == 404

    def test_live_geocode_provider_error_returns_503(self, company_client: TestClient, fake_geocoding_provider) -> None:
        fake_geocoding_provider.raise_exc = requests.RequestException("boom")
        response = company_client.get("/companii/99900002/coordonate")
        assert response.status_code == 503

    def test_has_rate_limiting(self, company_client: TestClient, fake_geocoding_provider) -> None:
        # sanity: the route exists and is reachable, exercising the decorator wiring
        fake_geocoding_provider.result = GeocodeResult(
            lat=1.0, lon=2.0, score=50.0, formatted_address="x", provider="arcgis",
        )
        response = company_client.get("/companii/99900002/coordonate")
        assert response.status_code == 200


# NOU: teste pentru GET /companii/{cui}/financiar
class TestCompanyFinanciar:
    """Covers the year-selection (ani vs. an_start/an_end vs. default-latest) and
    field-selection (campuri) behavior of the endpoint, plus its 404/400 cases."""

    def test_default_returns_latest_year_only(self, company_client: TestClient) -> None:
        response = company_client.get("/companii/12345784/financiar")
        assert response.status_code == 200
        payload = response.json()
        assert payload["cui"] == 12345784
        assert len(payload["years"]) == 1
        assert payload["years"][0]["an"] == 2023
        assert payload["years"][0]["values"]["cifra_afaceri"] == 150_000

    def test_ani_filter_returns_requested_years(self, company_client: TestClient) -> None:
        response = company_client.get("/companii/12345784/financiar", params={"ani": [2022, 2023]})
        assert response.status_code == 200
        payload = response.json()
        assert [year["an"] for year in payload["years"]] == [2023, 2022]

    def test_an_range_filter(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/12345784/financiar", params={"an_start": 2022, "an_end": 2022}
        )
        assert response.status_code == 200
        payload = response.json()
        assert [year["an"] for year in payload["years"]] == [2022]

    def test_invalid_an_range_returns_400(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/12345784/financiar", params={"an_start": 2023, "an_end": 2022}
        )
        assert response.status_code == 400

    def test_campuri_filter_restricts_values(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/12345784/financiar", params={"campuri": ["cifra_afaceri", "profit_net"]}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["fields"] == ["cifra_afaceri", "profit_net"]
        assert set(payload["years"][0]["values"]) == {"cifra_afaceri", "profit_net"}

    def test_unknown_field_returns_400(self, company_client: TestClient) -> None:
        response = company_client.get("/companii/12345784/financiar", params={"campuri": ["nu_exista"]})
        assert response.status_code == 400

    def test_unknown_cui_returns_404(self, company_client: TestClient) -> None:
        response = company_client.get("/companii/00000001/financiar")
        assert response.status_code == 404

    def test_company_without_financials_returns_404(self, company_client: TestClient) -> None:
        response = company_client.get("/companii/12345678/financiar")
        assert response.status_code == 404


# NOU: teste pentru GET /companii/{cui}/financiar/evolutie
class TestCompanyFinanciarEvolutie:
    def test_default_returns_all_years_ascending(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/12345784/financiar/evolutie", params={"camp": "cifra_afaceri"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["camp"] == "cifra_afaceri"
        assert [p["an"] for p in payload["puncte"]] == [2022, 2023]
        assert [p["valoare"] for p in payload["puncte"]] == [100_000, 150_000]

    def test_ani_filter(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/12345784/financiar/evolutie",
            params={"camp": "cifra_afaceri", "ani": [2023]},
        )
        assert response.status_code == 200
        payload = response.json()
        assert [p["an"] for p in payload["puncte"]] == [2023]

    def test_an_range_filter(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/12345784/financiar/evolutie",
            params={"camp": "profit_net", "an_start": 2022, "an_end": 2022},
        )
        assert response.status_code == 200
        payload = response.json()
        assert [p["an"] for p in payload["puncte"]] == [2022]
        assert payload["puncte"][0]["valoare"] == 10_000

    def test_unknown_field_returns_400(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/12345784/financiar/evolutie", params={"camp": "nu_exista"}
        )
        assert response.status_code == 400

    def test_unknown_cui_returns_404(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/00000001/financiar/evolutie", params={"camp": "cifra_afaceri"}
        )
        assert response.status_code == 404

    def test_company_without_financials_returns_404(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/12345678/financiar/evolutie", params={"camp": "cifra_afaceri"}
        )
        assert response.status_code == 404


# NOU: teste pentru GET /companii/financiar/clasament
class TestFinanciarClasament:
    def test_ranks_descending_by_field(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/financiar/clasament", params={"an": 2023, "camp": "cifra_afaceri", "limit": 3}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["an"] == 2023
        assert payload["camp"] == "cifra_afaceri"
        cuis = [row["cui"] for row in payload["results"]]
        assert cuis == [99900013, 99900011, 99900012]  # TREI (700k) > UNU (500k) > DOI (300k)

    def test_caen_filter(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/financiar/clasament",
            params={"an": 2023, "camp": "cifra_afaceri", "caen": "4711"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert [row["cui"] for row in payload["results"]] == [99900012]

    def test_county_filter(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/financiar/clasament",
            params={"an": 2023, "camp": "cifra_afaceri", "county": "Bucuresti"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert [row["cui"] for row in payload["results"]] == [99900012]

    def test_no_matches_returns_empty_list_not_404(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/financiar/clasament", params={"an": 1999, "camp": "cifra_afaceri"}
        )
        assert response.status_code == 200
        assert response.json()["results"] == []

    def test_unknown_field_returns_400(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/financiar/clasament", params={"an": 2023, "camp": "nu_exista"}
        )
        assert response.status_code == 400


# NOU: teste pentru GET /companii/{cui}/financiar/indicatori
class TestCompanyFinanciarIndicatori:
    def test_computes_margin_and_revenue_per_employee(self, company_client: TestClient) -> None:
        response = company_client.get("/companii/12345784/financiar/indicatori")
        assert response.status_code == 200
        payload = response.json()
        years = {y["an"]: y for y in payload["years"]}
        # 2022: cifra_afaceri=100_000, profit_net=10_000, numar_salariati=5
        assert years[2022]["marja_profit"] == pytest.approx(0.1)
        assert years[2022]["cifra_afaceri_per_salariat"] == pytest.approx(20_000)
        assert years[2022]["crestere_cifra_afaceri"] is None  # no earlier year in this response

    def test_computes_growth_relative_to_previous_returned_year(self, company_client: TestClient) -> None:
        response = company_client.get("/companii/12345784/financiar/indicatori")
        assert response.status_code == 200
        years = {y["an"]: y for y in response.json()["years"]}
        # 2023 vs 2022: cifra_afaceri 100_000 -> 150_000 (+50%), profit_net 10_000 -> 20_000 (+100%)
        assert years[2023]["crestere_cifra_afaceri"] == pytest.approx(0.5)
        assert years[2023]["crestere_profit_net"] == pytest.approx(1.0)

    def test_ani_filter_skips_growth_across_gap(self, company_client: TestClient) -> None:
        # Only 2023 requested: growth still computed relative to the nearest earlier
        # year actually returned -- since 2022 is excluded here, growth is null.
        response = company_client.get(
            "/companii/12345784/financiar/indicatori", params={"ani": [2023]}
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["years"]) == 1
        assert payload["years"][0]["crestere_cifra_afaceri"] is None

    def test_unknown_cui_returns_404(self, company_client: TestClient) -> None:
        response = company_client.get("/companii/00000001/financiar/indicatori")
        assert response.status_code == 404

    def test_company_without_financials_returns_404(self, company_client: TestClient) -> None:
        response = company_client.get("/companii/12345678/financiar/indicatori")
        assert response.status_code == 404
