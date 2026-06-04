"""Tests for /health, /, security headers, and rate limiting."""


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
        for _ in range(10):
            client.get("/health")
        assert client.get("/health").status_code == 429
        assert client.get("/health", headers={"X-API-KEY": valid_api_key}).status_code == 200

    def test_health_cache_is_no_store(self, client):
        cc = client.get("/health").headers.get("cache-control", "")
        assert "no-store" in cc
