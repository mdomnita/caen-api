"""Tests for /siruta endpoints.

Seeded localities (see conftest.py):
  cod 666 FOCSANI  tip_cod=12 (Municipiu) judet=41 VRANCEA
  cod 667 ADJUD    tip_cod=13 (Oras)      judet=41 VRANCEA
  cod 668 PANCIU   tip_cod=14 (Comuna)    judet=41 VRANCEA
  cod 100 BRASOV   tip_cod=12 (Municipiu) judet=10 BRASOV
"""


class TestJudete:
    def test_returns_all_counties(self, client):
        r = client.get("/siruta/judete")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 2

    def test_fields(self, client):
        assert set(client.get("/siruta/judete").json()[0].keys()) == {"cod_judet", "denumire"}

    def test_ordered_by_name(self, client):
        names = [j["denumire"] for j in client.get("/siruta/judete").json()]
        assert names == sorted(names)

    def test_has_cache_headers(self, client):
        r = client.get("/siruta/judete")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_matching_etag_returns_304(self, client):
        r1 = client.get("/siruta/judete")
        r2 = client.get("/siruta/judete", headers={"If-None-Match": r1.headers["etag"]})
        assert r2.status_code == 304

    def test_invalid_key_returns_403(self, client):
        assert client.get("/siruta/judete", headers={"X-API-KEY": "bad-key"}).status_code == 403


class TestLocalitate:
    def test_known_code_returns_entry(self, client):
        r = client.get("/siruta/localitate/666")
        assert r.status_code == 200
        body = r.json()
        assert body["cod_siruta"] == 666
        assert body["denumire"] == "FOCSANI"
        assert body["tip_cod"] == 12
        assert body["tip_abrev"] == "Mun."
        assert body["tip_denumire"] == "Municipiu"
        assert body["cod_judet"] == 41
        assert body["judet_denumire"] == "VRANCEA"

    def test_diacritics_field_included_in_response(self, client):
        # cached_json bypasses Pydantic, so denumire_diacritice is included raw
        body = client.get("/siruta/localitate/666").json()
        assert "denumire_diacritice" in body
        assert body["denumire_diacritice"] == "FOCŞANI"

    def test_unknown_code_returns_404(self, client):
        assert client.get("/siruta/localitate/99999").status_code == 404

    def test_404_detail_contains_code(self, client):
        assert "99999" in client.get("/siruta/localitate/99999").json()["detail"]

    def test_has_cache_headers(self, client):
        r = client.get("/siruta/localitate/666")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_matching_etag_returns_304(self, client):
        r1 = client.get("/siruta/localitate/666")
        r2 = client.get("/siruta/localitate/666", headers={"If-None-Match": r1.headers["etag"]})
        assert r2.status_code == 304


class TestCautare:
    def test_ascii_name_match(self, client):
        r = client.get("/siruta/cautare", params={"q": "FOCSANI"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["results"][0]["cod_siruta"] == 666

    def test_partial_name_match(self, client):
        r = client.get("/siruta/cautare", params={"q": "ADJ"})
        assert r.status_code == 200
        assert r.json()["results"][0]["cod_siruta"] == 667

    def test_no_match_returns_empty(self, client):
        body = client.get("/siruta/cautare", params={"q": "ZZNOTFOUND"}).json()
        assert body["total"] == 0
        assert body["results"] == []

    def test_q_too_short_returns_422(self, client):
        assert client.get("/siruta/cautare", params={"q": "F"}).status_code == 422

    def test_pagination_limit(self, client):
        # q="AN" matches FOCSANI and PANCIU
        body = client.get("/siruta/cautare", params={"q": "AN", "limit": 1}).json()
        assert body["total"] == 2
        assert len(body["results"]) == 1

    def test_pagination_offset(self, client):
        r1 = client.get("/siruta/cautare", params={"q": "AN", "limit": 1, "offset": 0})
        r2 = client.get("/siruta/cautare", params={"q": "AN", "limit": 1, "offset": 1})
        assert r1.json()["total"] == r2.json()["total"] == 2
        assert r1.json()["results"][0]["cod_siruta"] != r2.json()["results"][0]["cod_siruta"]

    def test_results_have_expected_fields(self, client):
        entry = client.get("/siruta/cautare", params={"q": "FOCSANI"}).json()["results"][0]
        for field in ("cod_siruta", "denumire", "tip_cod", "tip_abrev", "tip_denumire", "cod_judet", "judet_denumire"):
            assert field in entry

    def test_has_cache_headers(self, client):
        r = client.get("/siruta/cautare", params={"q": "FOCSANI"})
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers


class TestJudetLocalitati:
    def test_returns_all_localities_in_county(self, client):
        r = client.get("/siruta/judet/41")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 3
        codes = {e["cod_siruta"] for e in body}
        assert codes == {666, 667, 668}

    def test_ordered_by_tip_cod_then_name(self, client):
        body = client.get("/siruta/judet/41").json()
        assert body[0]["cod_siruta"] == 666  # Municipiu, tip_cod=12
        assert body[1]["cod_siruta"] == 667  # Oras, tip_cod=13
        assert body[2]["cod_siruta"] == 668  # Comuna, tip_cod=14

    def test_tip_cod_filter(self, client):
        r = client.get("/siruta/judet/41", params={"tip_cod": 12})
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["cod_siruta"] == 666

    def test_unknown_county_returns_empty_list(self, client):
        r = client.get("/siruta/judet/99")
        assert r.status_code == 200
        assert r.json() == []

    def test_different_counties_return_different_results(self, client):
        vrancea = {e["cod_siruta"] for e in client.get("/siruta/judet/41").json()}
        brasov = {e["cod_siruta"] for e in client.get("/siruta/judet/10").json()}
        assert vrancea.isdisjoint(brasov)

    def test_has_cache_headers(self, client):
        r = client.get("/siruta/judet/41")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_matching_etag_returns_304(self, client):
        r1 = client.get("/siruta/judet/41")
        r2 = client.get("/siruta/judet/41", headers={"If-None-Match": r1.headers["etag"]})
        assert r2.status_code == 304
