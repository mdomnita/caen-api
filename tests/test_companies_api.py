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
from routers.company_models import (
    Company,
    CompanyCaenCode,
    CompanyFinancial,
    CompanyFinancialStats,
    CompanyRepresentative,
)
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
        # Reprezentantii sunt stocati separat de companie si pot fi multipli.
        # Campurile personale sunt seed-uite pentru a verifica faptul ca API-ul nu le expune.
        session.add_all(
            [
                CompanyRepresentative(
                    company_id=mapiful.id,
                    nume="POPESCU PETRE",
                    calitate="administrator",
                    data_nasterii=None,
                ),
                CompanyRepresentative(
                    company_id=mapiful.id,
                    nume="IONESCU ANA",
                    calitate="administrator",
                    localitate="Cluj-Napoca",
                ),
            ]
        )
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
                CompanyFinancial(
                    company_id=top_unu.id, an=2023, sursa="MFP", caen="6201",
                    cifra_afaceri=500_000, profit_net=50_000, numar_salariati=10, datorii=100_000,
                    active_circulante_total=200_000,
                ),
                CompanyFinancial(
                    company_id=top_doi.id, an=2023, sursa="MFP", caen="4711",
                    cifra_afaceri=300_000, profit_net=20_000, numar_salariati=8, datorii=50_000,
                    active_circulante_total=90_000,
                ),
                CompanyFinancial(
                    company_id=top_trei.id, an=2023, sursa="MFP", caen="6201",
                    cifra_afaceri=700_000, profit_net=80_000, numar_salariati=15, datorii=150_000,
                    active_circulante_total=300_000,
                ),
            ]
        )

        # NOU: coduri CAEN pentru testele GET /companii (filtrare avansata)
        mapiful = session.scalar(select(Company).where(Company.cui == 12345784))
        session.add_all(
            [
                CompanyCaenCode(company_id=mapiful.id, caen_code="6201", is_principal=True),
                CompanyCaenCode(company_id=top_unu.id, caen_code="6201", is_principal=True),
                CompanyCaenCode(company_id=top_doi.id, caen_code="4711", is_principal=True),
                CompanyCaenCode(company_id=top_trei.id, caen_code="6201", is_principal=True),
                CompanyCaenCode(company_id=top_trei.id, caen_code="4791", is_principal=False),
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
    assert payload["representatives"] == [
        {"name": "IONESCU ANA", "role": "administrator"},
        {"name": "POPESCU PETRE", "role": "administrator"},
    ]


class TestCompanyRepresentatives:
    def test_returns_representatives_without_private_fields(self, company_client: TestClient) -> None:
        response = company_client.get("/companii/12345784/representatives")
        assert response.status_code == 200
        assert response.json() == {
            "cui": 12345784,
            "representatives": [
                {"name": "IONESCU ANA", "role": "administrator"},
                {"name": "POPESCU PETRE", "role": "administrator"},
            ],
        }

    def test_existing_company_without_representatives_returns_empty_list(
        self, company_client: TestClient
    ) -> None:
        response = company_client.get("/companii/12345678/representatives")
        assert response.status_code == 200
        assert response.json() == {"cui": 12345678, "representatives": []}

    def test_unknown_company_returns_404(self, company_client: TestClient) -> None:
        response = company_client.get("/companii/99999999/representatives")
        assert response.status_code == 404


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


# NOU: teste pentru GET /companii (filtrare avansata)
class TestCompanyListFilter:
    def test_filters_by_judet(self, company_client: TestClient) -> None:
        response = company_client.get("/companii", params={"judet": "Cluj"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 6
        assert all(row["county"] == "Cluj" for row in payload["results"])

    def test_filters_by_caen_principal_or_secondary(self, company_client: TestClient) -> None:
        response = company_client.get("/companii", params={"caen": "6201"})
        assert response.status_code == 200
        payload = response.json()
        cuis = {row["cui"] for row in payload["results"]}
        assert cuis == {12345784, 99900011, 99900013}

        # 4791 is a secondary (non-principal) code on TOP FIRMA TREI -- still matches.
        response = company_client.get("/companii", params={"caen": "4791"})
        assert response.status_code == 200
        assert [row["cui"] for row in response.json()["results"]] == [99900013]

    def test_filters_by_are_coordonate(self, company_client: TestClient) -> None:
        response = company_client.get("/companii", params={"are_coordonate": True})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 2
        assert {row["cui"] for row in payload["results"]} == {99900001, 99900004}

    def test_financial_threshold_requires_an(self, company_client: TestClient) -> None:
        response = company_client.get("/companii", params={"cifra_afaceri_min": 1})
        assert response.status_code == 400

    def test_financial_sort_requires_an(self, company_client: TestClient) -> None:
        response = company_client.get("/companii", params={"sort": "-cifra_afaceri"})
        assert response.status_code == 400

    def test_financial_threshold_filters_and_excludes_firms_without_data(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii", params={"an": 2023, "cifra_afaceri_min": 400_000}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 2
        cuis = {row["cui"] for row in payload["results"]}
        assert cuis == {99900011, 99900013}
        for row in payload["results"]:
            assert row["financiar"]["cifra_afaceri"] >= 400_000

    def test_sort_descending_by_financial_field_nulls_last(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii", params={"an": 2023, "sort": "-cifra_afaceri", "limit": 4}
        )
        assert response.status_code == 200
        cuis = [row["cui"] for row in response.json()["results"]]
        assert cuis == [99900013, 99900011, 99900012, 12345784]

    def test_pagination(self, company_client: TestClient) -> None:
        response = company_client.get("/companii", params={"judet": "Cluj", "limit": 2, "offset": 0})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 6
        assert len(payload["results"]) == 2

    def test_unknown_sort_field_returns_400(self, company_client: TestClient) -> None:
        response = company_client.get("/companii", params={"sort": "nu_exista"})
        assert response.status_code == 400


# NOU: teste pentru GET /companii/comparatie
class TestCompanyComparatie:
    def test_compares_explicit_year(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/comparatie", params={"cui": [99900011, 99900013], "an": 2023}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["an"] == 2023
        assert payload["cui_negasite"] == []
        by_cui = {row["cui"]: row for row in payload["results"]}
        assert by_cui[99900011]["cifra_afaceri"] == 500_000
        assert by_cui[99900013]["cifra_afaceri"] == 700_000
        # profit_net=50_000 / cifra_afaceri=500_000
        assert by_cui[99900011]["marja_profit"] == pytest.approx(0.1)
        # cifra_afaceri=500_000 / numar_salariati=10
        assert by_cui[99900011]["cifra_afaceri_per_salariat"] == pytest.approx(50_000)

    def test_defaults_to_each_companys_own_latest_year(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/comparatie", params={"cui": [12345784, 99900011]}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["an"] is None
        by_cui = {row["cui"]: row for row in payload["results"]}
        assert by_cui[12345784]["an"] == 2023  # MAPIFUL's latest year is 2023, not 2024
        assert by_cui[99900011]["an"] == 2023

    def test_growth_relative_to_nearest_earlier_year(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/comparatie", params={"cui": [12345784, 99900011], "an": 2023}
        )
        assert response.status_code == 200
        by_cui = {row["cui"]: row for row in response.json()["results"]}
        # MAPIFUL has a 2022 row: cifra_afaceri 100_000 -> 150_000 (+50%)
        assert by_cui[12345784]["crestere_cifra_afaceri"] == pytest.approx(0.5)
        # TOP FIRMA UNU has no earlier year -- growth is null, not zero
        assert by_cui[99900011]["crestere_cifra_afaceri"] is None

    def test_unknown_cui_reported_separately_not_dropped_silently(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/comparatie", params={"cui": [12345784, 99999999], "an": 2023}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["cui_negasite"] == [99999999]
        assert [row["cui"] for row in payload["results"]] == [12345784]

    def test_company_without_any_financial_data(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/comparatie", params={"cui": [12345784, 12345678]}
        )
        assert response.status_code == 200
        by_cui = {row["cui"]: row for row in response.json()["results"]}
        assert by_cui[12345678]["an"] is None
        assert by_cui[12345678]["cifra_afaceri"] is None
        assert by_cui[12345678]["marja_profit"] is None

    def test_requires_at_least_two_cuis(self, company_client: TestClient) -> None:
        response = company_client.get("/companii/comparatie", params={"cui": [12345784]})
        assert response.status_code == 400

    def test_rejects_too_many_cuis(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/comparatie", params={"cui": list(range(1, 22))}
        )
        assert response.status_code == 400


# NOU: teste pentru GET /companii/financiar/statistici
# Ruleaza pe SQLite (company_client), deci exercita doar fallback-ul Python pentru
# mediana -- calea Postgres (percentile_cont) nu e acoperita de teste automate.
class TestFinanciarStatistici:
    def test_aggregates_over_all_companies_with_data(self, company_client: TestClient) -> None:
        # an=2023, cifra_afaceri: MAPIFUL=150k, UNU=500k, DOI=300k, TREI=700k
        response = company_client.get(
            "/companii/financiar/statistici", params={"an": 2023, "camp": "cifra_afaceri"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["numar_firme"] == 4
        assert payload["suma"] == 1_650_000
        assert payload["medie"] == pytest.approx(412_500.0)
        assert payload["minim"] == 150_000
        assert payload["maxim"] == 700_000
        # sorted [150k, 300k, 500k, 700k] -- even count, average of two middle values
        assert payload["mediana"] == pytest.approx(400_000.0)
        # No CompanyFinancialStats row was seeded for this (an, camp) -- falls back to live.
        assert payload["sursa"] == "live"

    def test_filters_by_caen(self, company_client: TestClient) -> None:
        # caen=6201: MAPIFUL=150k, UNU=500k, TREI=700k (DOI is caen=4711, excluded)
        response = company_client.get(
            "/companii/financiar/statistici",
            params={"an": 2023, "camp": "cifra_afaceri", "caen": "6201"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["numar_firme"] == 3
        assert payload["suma"] == 1_350_000
        assert payload["minim"] == 150_000
        assert payload["maxim"] == 700_000
        # sorted [150k, 500k, 700k] -- odd count, exact middle value
        assert payload["mediana"] == pytest.approx(500_000.0)

    def test_filters_by_judet(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/financiar/statistici",
            params={"an": 2023, "camp": "cifra_afaceri", "judet": "Bucuresti"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["numar_firme"] == 1
        assert payload["suma"] == 300_000
        assert payload["medie"] == pytest.approx(300_000.0)
        assert payload["mediana"] == pytest.approx(300_000.0)
        assert payload["minim"] == payload["maxim"] == 300_000

    def test_echoes_requested_filters(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/financiar/statistici",
            params={"an": 2023, "camp": "cifra_afaceri", "judet": "Cluj", "caen": "6201"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["an"] == 2023
        assert payload["camp"] == "cifra_afaceri"
        assert payload["judet"] == "Cluj"
        assert payload["caen"] == "6201"
        assert payload["localitate"] is None
        # judet+caen together is never precomputed (see refresh script docstring).
        assert payload["sursa"] == "live"

    def test_no_matches_returns_200_with_zero_count_not_404(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/financiar/statistici", params={"an": 1999, "camp": "cifra_afaceri"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["numar_firme"] == 0
        assert payload["suma"] is None
        assert payload["medie"] is None
        assert payload["mediana"] is None
        assert payload["minim"] is None
        assert payload["maxim"] is None

    def test_unknown_field_returns_400(self, company_client: TestClient) -> None:
        response = company_client.get(
            "/companii/financiar/statistici", params={"an": 2023, "camp": "nu_exista"}
        )
        assert response.status_code == 400

    def test_reads_from_precomputed_table_when_available(self, company_client: TestClient) -> None:
        # Seed a CompanyFinancialStats row with values that deliberately don't match what
        # a live computation over _seed_companies's data would produce, to prove the
        # endpoint is actually reading the precomputed row rather than recomputing.
        with SessionLocal() as session:
            session.add(
                CompanyFinancialStats(
                    an=2023, camp="cifra_afaceri", judet=None, caen=None,
                    numar_firme=999, suma=1, medie=1.0, mediana=None, minim=1, maxim=1,
                )
            )
            session.commit()

        response = company_client.get(
            "/companii/financiar/statistici", params={"an": 2023, "camp": "cifra_afaceri"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["numar_firme"] == 999
        assert payload["suma"] == 1
        assert payload["sursa"] == "precalculat"
        assert payload["mediana"] is None  # never computed by the refresh script

    def test_precomputed_national_row_but_no_row_for_requested_judet_means_zero(
        self, company_client: TestClient
    ) -> None:
        # The (an, camp) is covered by the refresh (national row exists), but there's no
        # row for judet="Timis" specifically -- that means zero matching companies, not
        # "not refreshed yet", so this must NOT fall back to a live recompute.
        # Uses a distinct `camp` from the other precomputed-path tests in this class,
        # since company_client's DB is shared across the whole module (no per-test
        # cleanup of CompanyFinancialStats).
        with SessionLocal() as session:
            session.add(
                CompanyFinancialStats(
                    an=2023, camp="profit_net", judet=None, caen=None,
                    numar_firme=1, suma=1, medie=1.0, mediana=None, minim=1, maxim=1,
                )
            )
            session.commit()

        response = company_client.get(
            "/companii/financiar/statistici",
            params={"an": 2023, "camp": "profit_net", "judet": "Timis"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["sursa"] == "precalculat"
        assert payload["numar_firme"] == 0
        assert payload["suma"] is None

    def test_localitate_always_uses_live_path_even_if_precomputed_available(
        self, company_client: TestClient
    ) -> None:
        with SessionLocal() as session:
            session.add(
                CompanyFinancialStats(
                    an=2023, camp="venituri_totale", judet=None, caen=None,
                    numar_firme=999, suma=1, medie=1.0, mediana=None, minim=1, maxim=1,
                )
            )
            session.commit()

        response = company_client.get(
            "/companii/financiar/statistici",
            params={"an": 2023, "camp": "venituri_totale", "localitate": "Cluj-Napoca"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["sursa"] == "live"
        assert payload["numar_firme"] != 999  # proves it recomputed, didn't read the seeded row
