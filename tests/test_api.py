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


class TestCaching:
    def test_data_endpoint_has_cache_control(self, client):
        r = client.get("/caen/0111")
        cc = r.headers.get("cache-control", "")
        assert "public" in cc
        assert "max-age=" in cc

    def test_data_endpoint_has_etag(self, client):
        r = client.get("/caen/0111")
        assert "etag" in r.headers

    def test_matching_etag_returns_304(self, client):
        r1 = client.get("/caen/0111")
        etag = r1.headers["etag"]
        r2 = client.get("/caen/0111", headers={"If-None-Match": etag})
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

    def test_health_has_no_store(self, client):
        cc = client.get("/health").headers.get("cache-control", "")
        assert "no-store" in cc


class TestSectiuni:
    def test_list_returns_all_sections(self, client):
        r = client.get("/sectiuni")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["cod"] == "A"
        assert body[0]["denumire"] == "Agricultura, silvicultura si pescuit"

    def test_list_item_has_only_cod_and_denumire(self, client):
        body = client.get("/sectiuni").json()
        assert set(body[0].keys()) == {"cod", "denumire"}

    def test_get_known_section(self, client):
        r = client.get("/sectiuni/A")
        assert r.status_code == 200
        assert r.json()["cod"] == "A"

    def test_get_section_case_insensitive(self, client):
        r = client.get("/sectiuni/a")
        assert r.status_code == 200
        assert r.json()["cod"] == "A"

    def test_get_unknown_section_returns_404(self, client):
        assert client.get("/sectiuni/Z").status_code == 404

    def test_list_has_cache_headers(self, client):
        r = client.get("/sectiuni")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_invalid_section_pattern_returns_422(self, client):
        assert client.get("/sectiuni/123").status_code == 422


class TestDiviziuni:
    def test_list_by_section_returns_divisions(self, client):
        r = client.get("/sectiuni/A/diviziuni")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["cod"] == "01"
        assert body[0]["sectiune_cod"] == "A"

    def test_list_item_has_expected_fields(self, client):
        body = client.get("/sectiuni/A/diviziuni").json()
        assert set(body[0].keys()) == {"cod", "denumire", "sectiune_cod"}

    def test_unknown_section_returns_404(self, client):
        assert client.get("/sectiuni/Z/diviziuni").status_code == 404

    def test_get_known_division(self, client):
        r = client.get("/diviziuni/01")
        assert r.status_code == 200
        body = r.json()
        assert body["cod"] == "01"
        assert body["sectiune_cod"] == "A"

    def test_get_unknown_division_returns_404(self, client):
        assert client.get("/diviziuni/99").status_code == 404

    def test_invalid_division_pattern_returns_422(self, client):
        assert client.get("/diviziuni/1").status_code == 422

    def test_get_division_has_cache_headers(self, client):
        r = client.get("/diviziuni/01")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers


class TestGrupe:
    def test_list_by_division_returns_groups(self, client):
        r = client.get("/diviziuni/01/grupe")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["cod"] == "011"
        assert body[0]["diviziune_cod"] == "01"

    def test_list_item_has_expected_fields(self, client):
        body = client.get("/diviziuni/01/grupe").json()
        assert set(body[0].keys()) == {"cod", "denumire", "diviziune_cod"}

    def test_unknown_division_returns_404(self, client):
        assert client.get("/diviziuni/99/grupe").status_code == 404

    def test_get_known_group(self, client):
        r = client.get("/grupe/011")
        assert r.status_code == 200
        body = r.json()
        assert body["cod"] == "011"
        assert body["diviziune_cod"] == "01"

    def test_get_unknown_group_returns_404(self, client):
        assert client.get("/grupe/999").status_code == 404

    def test_invalid_group_pattern_returns_422(self, client):
        assert client.get("/grupe/01").status_code == 422

    def test_get_group_has_cache_headers(self, client):
        r = client.get("/grupe/011")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers


class TestClasePrinGrupa:
    def test_list_returns_all_classes_in_group(self, client):
        r = client.get("/grupe/011/clase")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 2
        codes = [e["cod_caen"] for e in body]
        assert "0111" in codes
        assert "0112" in codes

    def test_list_results_have_full_caen_entry_fields(self, client):
        body = client.get("/grupe/011/clase").json()
        assert set(body[0].keys()) == {
            "cod_caen", "denumire",
            "sectiune_cod", "sectiune",
            "diviziune_cod", "diviziune",
            "grupa_cod", "grupa",
        }

    def test_results_ordered_by_code(self, client):
        body = client.get("/grupe/011/clase").json()
        codes = [e["cod_caen"] for e in body]
        assert codes == sorted(codes)

    def test_unknown_group_returns_404(self, client):
        assert client.get("/grupe/999/clase").status_code == 404

    def test_has_cache_headers(self, client):
        r = client.get("/grupe/011/clase")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_matching_etag_returns_304(self, client):
        r1 = client.get("/grupe/011/clase")
        r2 = client.get("/grupe/011/clase", headers={"If-None-Match": r1.headers["etag"]})
        assert r2.status_code == 304


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
