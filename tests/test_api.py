"""
Integration tests for the CAEN REST API.

All tests run against a seeded in-memory SQLite database (see conftest.py).
Rate limit counters are reset before each test by the autouse fixture.
"""


class TestHealth:
    def test_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestRoot:
    def test_redirects_to_docs(self, client):
        r = client.get("/", follow_redirects=False)
        assert r.status_code in (301, 302, 307, 308)
        assert "docs" in r.headers["location"]


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
        r = client.get("/caen/0111")
        assert set(r.json().keys()) == {
            "cod_caen", "denumire",
            "sectiune_cod", "sectiune",
            "diviziune_cod", "diviziune",
            "grupa_cod", "grupa",
        }

    def test_unknown_code_returns_404(self, client):
        r = client.get("/caen/9999")
        assert r.status_code == 404

    def test_404_detail_contains_the_code(self, client):
        r = client.get("/caen/9999")
        assert "9999" in r.json()["detail"]

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
        assert r.status_code == 200
        assert r.json()["total"] == 2

    def test_name_substring_match(self, client):
        r = client.get("/caen", params={"q": "cereale"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["results"][0]["cod_caen"] == "0111"

    def test_name_search_is_case_insensitive(self, client):
        # SQLite LIKE is case-insensitive for ASCII
        r = client.get("/caen", params={"q": "CEREALE"})
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_no_match_returns_empty_list(self, client):
        r = client.get("/caen", params={"q": "zzznotfound"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 0
        assert body["results"] == []

    def test_pagination_limit(self, client):
        r = client.get("/caen", params={"q": "011", "limit": 1})
        body = r.json()
        assert body["total"] == 2       # total reflects full match count
        assert len(body["results"]) == 1

    def test_pagination_offset_returns_different_page(self, client):
        page1 = client.get("/caen", params={"q": "011", "limit": 1, "offset": 0}).json()
        page2 = client.get("/caen", params={"q": "011", "limit": 1, "offset": 1}).json()
        assert page1["total"] == page2["total"] == 2
        assert page1["results"][0]["cod_caen"] != page2["results"][0]["cod_caen"]

    def test_results_ordered_by_code(self, client):
        r = client.get("/caen", params={"q": "011"})
        codes = [e["cod_caen"] for e in r.json()["results"]]
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
        r = client.get("/caen/0111", headers={"X-API-KEY": valid_api_key})
        assert r.status_code == 200

    def test_invalid_key_returns_403(self, client):
        r = client.get("/caen/0111", headers={"X-API-KEY": "bad-key"})
        assert r.status_code == 403

    def test_403_applies_to_search_too(self, client):
        r = client.get("/caen", params={"q": "0111"}, headers={"X-API-KEY": "bad-key"})
        assert r.status_code == 403

    def test_valid_key_on_search(self, client, valid_api_key):
        r = client.get("/caen", params={"q": "0111"}, headers={"X-API-KEY": valid_api_key})
        assert r.status_code == 200


class TestSecurityHeaders:
    def test_x_content_type_options(self, client):
        assert client.get("/health").headers["x-content-type-options"] == "nosniff"

    def test_x_frame_options(self, client):
        assert client.get("/health").headers["x-frame-options"] == "DENY"

    def test_referrer_policy(self, client):
        assert client.get("/health").headers["referrer-policy"] == "strict-origin-when-cross-origin"

    def test_xss_protection_disabled(self, client):
        # Modern guidance is to disable the broken XSS auditor
        assert client.get("/health").headers["x-xss-protection"] == "0"

    def test_headers_present_on_data_endpoints(self, client):
        r = client.get("/caen/0111")
        assert "x-content-type-options" in r.headers
        assert "x-frame-options" in r.headers


class TestRateLimiting:
    def test_anonymous_limited_after_10_requests(self, client):
        for _ in range(10):
            assert client.get("/health").status_code == 200
        assert client.get("/health").status_code == 429

    def test_authenticated_not_limited_at_10(self, client, valid_api_key):
        headers = {"X-API-KEY": valid_api_key}
        for _ in range(15):
            assert client.get("/health", headers=headers).status_code == 200

    def test_429_response_is_json(self, client):
        for _ in range(10):
            client.get("/health")
        r = client.get("/health")
        assert r.status_code == 429
        assert r.headers["content-type"].startswith("application/json")

    def test_authenticated_and_anonymous_have_separate_buckets(self, client, valid_api_key):
        # Exhaust anonymous quota
        for _ in range(10):
            client.get("/health")
        assert client.get("/health").status_code == 429

        # Authenticated bucket is untouched
        r = client.get("/health", headers={"X-API-KEY": valid_api_key})
        assert r.status_code == 200
