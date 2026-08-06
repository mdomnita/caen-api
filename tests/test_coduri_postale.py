"""Tests for /coduripostale endpoints.

Seeded rows (see conftest.py):
  011357  bucuresti  Mincu Ion, arh.    nr. 21-T   sector 1  (open-ended, impar)
  011357  bucuresti  Porumbaru Emanoil  nr. 1-25   sector 1  (duplicate cod_postal)
  620032  oras       Focsani/Vrancea    Cuza Voda  nr. 2-24  (closed, par)
  620033  oras       Focsani/Vrancea    Cuza Voda  bl. T1, T2 (no parsed range)
  500001  oras       Brasov/Brasov      Eroilor    nr. 1-T   (open-ended, impar)
  625200  sat        Panciu/Vrancea     (no street data)
  625301  sat        Straoane (Panciu)/Vrancea, cod_siruta NULL
"""
import pytest
import requests

from main import app
from routers.coduripostale import get_geocoding_provider
from services.geocoding import GeocodeResult


class _FakeProvider:
    """Test double for GeocodingProvider — never touches the network."""

    def __init__(self):
        self.calls: list[str] = []
        self.result: GeocodeResult | None = None
        self.raise_exc: Exception | None = None

    def geocode(self, address: str) -> GeocodeResult | None:
        self.calls.append(address)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.result


@pytest.fixture
def fake_provider():
    provider = _FakeProvider()
    app.dependency_overrides[get_geocoding_provider] = lambda: provider
    yield provider
    app.dependency_overrides.pop(get_geocoding_provider, None)


class TestGetByCodPostal:
    def test_known_code_returns_list(self, client):
        r = client.get("/coduripostale/011357")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 2

    def test_duplicate_code_returns_both_streets(self, client):
        body = client.get("/coduripostale/011357").json()
        streets = {row["strada_raw"] for row in body}
        assert streets == {"Mincu Ion, arh.", "Porumbaru Emanoil"}

    def test_single_match_returns_single_item_list(self, client):
        body = client.get("/coduripostale/620032").json()
        assert len(body) == 1
        assert body[0]["localitate_raw"] == "Focșani"

    def test_unknown_code_returns_404(self, client):
        assert client.get("/coduripostale/999999").status_code == 404

    def test_non_6_digit_code_returns_422(self, client):
        assert client.get("/coduripostale/123").status_code == 422

    def test_numar_open_ended_is_bool(self, client):
        body = client.get("/coduripostale/500001").json()
        assert body[0]["numar_open_ended"] is True

    def test_numar_closed_is_bool_false(self, client):
        body = client.get("/coduripostale/620032").json()
        assert body[0]["numar_open_ended"] is False

    def test_sat_row_has_null_street_fields(self, client):
        body = client.get("/coduripostale/625200").json()
        assert body[0]["strada_raw"] is None
        assert body[0]["sursa"] == "sat"

    def test_sat_row_with_parent_locality(self, client):
        body = client.get("/coduripostale/625301").json()
        assert body[0]["localitate_raw"] == "Straoane"
        assert body[0]["localitate_parinte_raw"] == "Panciu"
        assert body[0]["cod_siruta"] is None

    def test_has_cache_headers(self, client):
        r = client.get("/coduripostale/011357")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_matching_etag_returns_304(self, client):
        r1 = client.get("/coduripostale/011357")
        r2 = client.get("/coduripostale/011357", headers={"If-None-Match": r1.headers["etag"]})
        assert r2.status_code == 304

    def test_invalid_key_returns_403(self, client):
        assert client.get("/coduripostale/011357", headers={"X-API-KEY": "bad-key"}).status_code == 403


class TestCautare:
    def test_no_filters_returns_400(self, client):
        assert client.get("/coduripostale/cautare").status_code == 400

    def test_filter_by_judet(self, client):
        r = client.get("/coduripostale/cautare", params={"judet": "Vrancea"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 4  # 620032, 620033, 625200, 625301
        assert all(row["judet_norm"] == "VRANCEA" for row in body["results"])

    def test_filter_by_judet_diacritics_insensitive(self, client):
        # seeded as raw 'Vrancea' (no diacritics in source); querying with the
        # diacritic spelling should still match via normalize_search().
        r = client.get("/coduripostale/cautare", params={"judet": "Vrâncea"})
        assert r.json()["total"] == 4

    def test_filter_by_judet_no_match_returns_zero(self, client):
        r = client.get("/coduripostale/cautare", params={"judet": "Vranceaua"})
        assert r.json()["total"] == 0

    def test_filter_by_localitate(self, client):
        r = client.get("/coduripostale/cautare", params={"localitate": "Focsani"})
        body = r.json()
        assert body["total"] == 2
        assert {row["cod_postal"] for row in body["results"]} == {"620032", "620033"}

    def test_filter_by_strada_substring(self, client):
        r = client.get("/coduripostale/cautare", params={"strada": "cuza"})
        body = r.json()
        assert body["total"] == 2

    def test_filter_by_numar_matches_closed_range(self, client):
        r = client.get("/coduripostale/cautare", params={"numar": 10})
        body = r.json()
        codes = {row["cod_postal"] for row in body["results"]}
        assert "620032" in codes  # nr. 2-24 (par), 10 is even
        assert "620033" not in codes  # bl. row, no parsed range

    def test_filter_by_numar_excludes_wrong_parity_closed_range(self, client):
        # 620032 is nr. 2-24 (par); 11 is odd and within [2,24] but should not match
        r = client.get("/coduripostale/cautare", params={"numar": 11})
        codes = {row["cod_postal"] for row in r.json()["results"]}
        assert "620032" not in codes

    def test_filter_by_numar_matches_open_ended_range(self, client):
        # 500001 is nr. 1-T (impar); 501 is odd
        r = client.get("/coduripostale/cautare", params={"numar": 501})
        codes = {row["cod_postal"] for row in r.json()["results"]}
        assert "500001" in codes

    def test_filter_by_numar_excludes_wrong_parity_open_ended_range(self, client):
        # 500001 is nr. 1-T (impar); 500 is even and >= 1 but should not match
        r = client.get("/coduripostale/cautare", params={"numar": 500})
        codes = {row["cod_postal"] for row in r.json()["results"]}
        assert "500001" not in codes

    def test_filter_by_numar_excludes_out_of_range(self, client):
        r = client.get("/coduripostale/cautare", params={"numar": 100})
        codes = {row["cod_postal"] for row in r.json()["results"]}
        assert "620032" not in codes  # nr. 2-24 does not contain 100

    def test_combined_judet_and_localitate(self, client):
        # localitate filter matches localitate_norm exactly; 625301's
        # localitate is 'Straoane' (parent 'Panciu' is a separate column,
        # not matched here), so only 625200 (locality itself 'Panciu') matches.
        r = client.get("/coduripostale/cautare", params={"judet": "Vrancea", "localitate": "Panciu"})
        body = r.json()
        assert body["total"] == 1
        assert body["results"][0]["cod_postal"] == "625200"

    def test_pagination_limit(self, client):
        body = client.get("/coduripostale/cautare", params={"judet": "Vrancea", "limit": 2}).json()
        assert body["total"] == 4
        assert len(body["results"]) == 2

    def test_pagination_offset(self, client):
        r1 = client.get("/coduripostale/cautare", params={"judet": "Vrancea", "limit": 1, "offset": 0})
        r2 = client.get("/coduripostale/cautare", params={"judet": "Vrancea", "limit": 1, "offset": 1})
        assert r1.json()["results"][0]["id"] != r2.json()["results"][0]["id"]

    def test_has_cache_headers(self, client):
        r = client.get("/coduripostale/cautare", params={"judet": "Vrancea"})
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers


class TestAutocomplete:
    def test_judet_prefix(self, client):
        r = client.get("/coduripostale/autocomplete", params={"tip": "judet", "q": "Vra"})
        assert r.status_code == 200
        assert "Vrancea" in r.json()["results"]

    def test_localitate_prefix(self, client):
        r = client.get("/coduripostale/autocomplete", params={"tip": "localitate", "q": "Foc"})
        assert "Focșani" in r.json()["results"]

    def test_strada_requires_localitate(self, client):
        r = client.get("/coduripostale/autocomplete", params={"tip": "strada", "q": "Cuz"})
        assert r.status_code == 400

    def test_strada_scoped_to_localitate(self, client):
        r = client.get(
            "/coduripostale/autocomplete",
            params={"tip": "strada", "q": "Cuz", "localitate": "Focsani"},
        )
        assert r.status_code == 200
        assert r.json()["results"] == ["Cuza Vodă"]

    def test_invalid_tip_returns_422(self, client):
        assert client.get("/coduripostale/autocomplete", params={"tip": "strazi", "q": "ab"}).status_code == 422

    def test_q_too_short_returns_422(self, client):
        assert client.get("/coduripostale/autocomplete", params={"tip": "judet", "q": "V"}).status_code == 422

    def test_no_match_returns_empty(self, client):
        body = client.get("/coduripostale/autocomplete", params={"tip": "judet", "q": "ZZ"}).json()
        assert body["results"] == []

    def test_has_cache_headers(self, client):
        r = client.get("/coduripostale/autocomplete", params={"tip": "judet", "q": "Vra"})
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers


class TestRezolvare:
    def test_local_match_with_street_no_provider_call(self, client, fake_provider):
        r = client.get("/coduripostale/rezolvare", params={"adresa": "Str. Cuza Voda, Focsani, Vrancea"})
        assert r.status_code == 200
        body = r.json()
        assert body["query"] == "Str. Cuza Voda, Focsani, Vrancea"
        assert body["candidates"]
        assert all(c["source"] == "local" for c in body["candidates"])
        assert body["candidates"][0]["localitate"] == "Focșani"
        assert body["candidates"][0]["strada"] == "Cuza Vodă"
        assert fake_provider.calls == []  # local match found, provider never invoked

    def test_local_match_locality_only_no_street_token(self, client, fake_provider):
        r = client.get("/coduripostale/rezolvare", params={"adresa": "Panciu, judetul Vrancea"})
        body = r.json()
        assert len(body["candidates"]) == 1
        assert body["candidates"][0]["localitate"] == "Panciu"
        assert body["candidates"][0]["strada"] is None
        assert body["candidates"][0]["cod_postal"] == "625200"
        assert fake_provider.calls == []

    def test_no_local_match_falls_back_to_provider(self, client, fake_provider):
        fake_provider.result = GeocodeResult(
            lat=44.4, lon=26.1, score=88.5, formatted_address="Some St 1, Some City", provider="arcgis",
        )
        r = client.get("/coduripostale/rezolvare", params={"adresa": "Adresa complet necunoscuta xyz123"})
        assert r.status_code == 200
        body = r.json()
        assert len(body["candidates"]) == 1
        candidate = body["candidates"][0]
        assert candidate["source"] == "provider:arcgis"
        assert candidate["lat"] == 44.4
        assert candidate["lon"] == 26.1
        assert candidate["formatted_address"] == "Some St 1, Some City"
        assert fake_provider.calls == ["Adresa complet necunoscuta xyz123"]

    def test_no_local_match_and_provider_returns_none(self, client, fake_provider):
        fake_provider.result = None
        r = client.get("/coduripostale/rezolvare", params={"adresa": "Adresa complet necunoscuta xyz123"})
        assert r.status_code == 200
        assert r.json()["candidates"] == []
        assert fake_provider.calls == ["Adresa complet necunoscuta xyz123"]

    def test_no_local_match_and_provider_raises_returns_empty(self, client, fake_provider):
        fake_provider.raise_exc = requests.RequestException("boom")
        r = client.get("/coduripostale/rezolvare", params={"adresa": "Adresa complet necunoscuta xyz123"})
        assert r.status_code == 200
        assert r.json()["candidates"] == []

    def test_adresa_too_short_returns_422(self, client):
        assert client.get("/coduripostale/rezolvare", params={"adresa": "abc"}).status_code == 422

    def test_has_cache_headers(self, client, fake_provider):
        r = client.get("/coduripostale/rezolvare", params={"adresa": "Panciu, judetul Vrancea"})
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_invalid_key_returns_403(self, client):
        assert client.get(
            "/coduripostale/rezolvare",
            params={"adresa": "Panciu, judetul Vrancea"},
            headers={"X-API-KEY": "bad-key"},
        ).status_code == 403
