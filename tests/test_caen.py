"""Tests for /caen endpoints."""


class TestGetByCode:
    def test_known_code_returns_full_entry(self, client):
        r = client.get("/caen/0111")
        assert r.status_code == 200
        body = r.json()
        assert body["cod_caen"] == "0111"
        assert body["denumire"] == "Cultivarea cerealelor"
        assert body["sectiune_cod"] == "A"
        assert body["diviziune_cod"] == "01"
        assert body["grupa_cod"] == "011"

    def test_response_contains_all_fields(self, client):
        assert set(client.get("/caen/0111").json().keys()) == {
            "cod_caen", "denumire",
            "sectiune_cod", "sectiune",
            "diviziune_cod", "diviziune",
            "grupa_cod", "grupa",
        }

    def test_unknown_code_returns_404(self, client):
        assert client.get("/caen/9999").status_code == 404

    def test_404_detail_contains_the_code(self, client):
        assert "9999" in client.get("/caen/9999").json()["detail"]

    def test_second_seeded_code(self, client):
        r = client.get("/caen/0112")
        assert r.status_code == 200
        assert r.json()["cod_caen"] == "0112"


class TestSearch:
    def test_exact_code_match(self, client):
        r = client.get("/caen", params={"q": "0111"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["results"][0]["cod_caen"] == "0111"

    def test_partial_code_returns_all_matches(self, client):
        r = client.get("/caen", params={"q": "011"})
        assert r.json()["total"] == 2

    def test_name_substring_match(self, client):
        r = client.get("/caen", params={"q": "cereale"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["results"][0]["cod_caen"] == "0111"

    def test_name_search_is_case_insensitive(self, client):
        # SQLite LIKE is case-insensitive for ASCII
        assert client.get("/caen", params={"q": "CEREALE"}).json()["total"] == 1

    def test_no_match_returns_empty_list(self, client):
        body = client.get("/caen", params={"q": "zzznotfound"}).json()
        assert body["total"] == 0
        assert body["results"] == []

    def test_pagination_limit(self, client):
        body = client.get("/caen", params={"q": "011", "limit": 1}).json()
        assert body["total"] == 2
        assert len(body["results"]) == 1

    def test_pagination_offset_returns_different_page(self, client):
        page1 = client.get("/caen", params={"q": "011", "limit": 1, "offset": 0}).json()
        page2 = client.get("/caen", params={"q": "011", "limit": 1, "offset": 1}).json()
        assert page1["total"] == page2["total"] == 2
        assert page1["results"][0]["cod_caen"] != page2["results"][0]["cod_caen"]

    def test_results_ordered_by_code(self, client):
        codes = [e["cod_caen"] for e in client.get("/caen", params={"q": "011"}).json()["results"]]
        assert codes == sorted(codes)

    def test_missing_q_returns_422(self, client):
        assert client.get("/caen").status_code == 422

    def test_empty_q_returns_422(self, client):
        assert client.get("/caen", params={"q": ""}).status_code == 422

    def test_limit_zero_returns_422(self, client):
        assert client.get("/caen", params={"q": "0", "limit": 0}).status_code == 422

    def test_limit_above_max_returns_422(self, client):
        assert client.get("/caen", params={"q": "0", "limit": 201}).status_code == 422

    def test_negative_offset_returns_422(self, client):
        assert client.get("/caen", params={"q": "0", "offset": -1}).status_code == 422


class TestApiKeyAuth:
    def test_no_key_is_accepted(self, client):
        assert client.get("/caen/0111").status_code == 200

    def test_valid_key_is_accepted(self, client, valid_api_key):
        assert client.get("/caen/0111", headers={"X-API-KEY": valid_api_key}).status_code == 200

    def test_invalid_key_returns_403(self, client):
        assert client.get("/caen/0111", headers={"X-API-KEY": "bad-key"}).status_code == 403

    def test_403_applies_to_search_too(self, client):
        assert client.get("/caen", params={"q": "0111"}, headers={"X-API-KEY": "bad-key"}).status_code == 403

    def test_valid_key_on_search(self, client, valid_api_key):
        assert client.get("/caen", params={"q": "0111"}, headers={"X-API-KEY": valid_api_key}).status_code == 200


class TestCaching:
    def test_data_endpoint_has_cache_control(self, client):
        cc = client.get("/caen/0111").headers.get("cache-control", "")
        assert "public" in cc
        assert "max-age=" in cc

    def test_data_endpoint_has_etag(self, client):
        assert "etag" in client.get("/caen/0111").headers

    def test_matching_etag_returns_304(self, client):
        r1 = client.get("/caen/0111")
        r2 = client.get("/caen/0111", headers={"If-None-Match": r1.headers["etag"]})
        assert r2.status_code == 304
        assert r2.content == b""

    def test_stale_etag_returns_200_with_body(self, client):
        r = client.get("/caen/0111", headers={"If-None-Match": '"stale"'})
        assert r.status_code == 200
        assert r.json()["cod_caen"] == "0111"

    def test_search_has_cache_control_and_etag(self, client):
        r = client.get("/caen", params={"q": "0111"})
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_search_matching_etag_returns_304(self, client):
        r1 = client.get("/caen", params={"q": "0111"})
        r2 = client.get("/caen", params={"q": "0111"}, headers={"If-None-Match": r1.headers["etag"]})
        assert r2.status_code == 304

    def test_different_queries_produce_different_etags(self, client):
        e1 = client.get("/caen/0111").headers["etag"]
        e2 = client.get("/caen/0112").headers["etag"]
        assert e1 != e2
