"""Tests for /localitati endpoints.

Seeded localities (see conftest.py):
  gid 1 Focșani      judet=Vrancea    lat=45.6967 lon=27.1858
  gid 2 Independența judet=Constanța  lat=44.2833 lon=27.7000
  gid 3 Independența judet=Galați     lat=45.7333 lon=27.9333
"""


class TestSearch:
    def test_diacritics_query_matches(self, client):
        r = client.get("/localitati/search", params={"q": "Focșani"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["results"][0]["gid"] == 1

    def test_ascii_query_matches_diacritics_name(self, client):
        r = client.get("/localitati/search", params={"q": "Focsani"})
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_partial_query_matches_multiple(self, client):
        r = client.get("/localitati/search", params={"q": "Independen"})
        assert r.status_code == 200
        assert r.json()["total"] == 2

    def test_no_match_returns_empty(self, client):
        body = client.get("/localitati/search", params={"q": "ZZNOTFOUND"}).json()
        assert body["total"] == 0
        assert body["results"] == []

    def test_q_too_short_returns_422(self, client):
        assert client.get("/localitati/search", params={"q": "F"}).status_code == 422

    def test_pagination_limit(self, client):
        body = client.get("/localitati/search", params={"q": "Independen", "limit": 1}).json()
        assert body["total"] == 2
        assert len(body["results"]) == 1

    def test_results_have_expected_fields(self, client):
        entry = client.get("/localitati/search", params={"q": "Focsani"}).json()["results"][0]
        for field in ("gid", "nume_uat", "natlevname", "natcode", "judet", "lat", "lon"):
            assert field in entry

    def test_has_cache_headers(self, client):
        r = client.get("/localitati/search", params={"q": "Focsani"})
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers


class TestLocalitate:
    def test_unique_name_returns_single_entry_list(self, client):
        r = client.get("/localitati/localitate/Focșani")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["nume_uat"] == "Focșani"
        assert body[0]["lat"] == 45.6967
        assert body[0]["lon"] == 27.1858

    def test_ascii_name_matches_diacritics_entry(self, client):
        r = client.get("/localitati/localitate/Focsani")
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_colliding_name_returns_all_matches(self, client):
        r = client.get("/localitati/localitate/Independenta")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 2
        judete = {e["judet"] for e in body}
        assert judete == {"Constanța", "Galați"}

    def test_judet_query_param_disambiguates(self, client):
        r = client.get("/localitati/localitate/Independenta", params={"judet": "Galati"})
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["judet"] == "Galați"

    def test_unknown_name_returns_404(self, client):
        assert client.get("/localitati/localitate/ZZNOTFOUND").status_code == 404

    def test_404_detail_contains_name(self, client):
        assert "ZZNOTFOUND" in client.get("/localitati/localitate/ZZNOTFOUND").json()["detail"]

    def test_has_cache_headers(self, client):
        r = client.get("/localitati/localitate/Focsani")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers


class TestJudet:
    def test_returns_all_localities_in_county(self, client):
        r = client.get("/localitati/judet/Constanta")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["gid"] == 2

    def test_diacritics_judet_name_matches(self, client):
        r = client.get("/localitati/judet/Galați")
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_unknown_county_returns_404(self, client):
        assert client.get("/localitati/judet/ZZNOTFOUND").status_code == 404

    def test_has_cache_headers(self, client):
        r = client.get("/localitati/judet/Vrancea")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_matching_etag_returns_304(self, client):
        r1 = client.get("/localitati/judet/Vrancea")
        r2 = client.get("/localitati/judet/Vrancea", headers={"If-None-Match": r1.headers["etag"]})
        assert r2.status_code == 304
