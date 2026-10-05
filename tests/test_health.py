"""Tests for /health, /, security headers, and rate limiting."""

import os
import sqlite3

import auth


def _fetch_one(query, params=()):
    conn = sqlite3.connect(os.environ["SQLITE_DB"])
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query, params).fetchone()
    finally:
        conn.close()


class TestHealth:
    def test_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_request_is_logged_to_database(self, client):
        before = _fetch_one(
            "SELECT COUNT(*) AS total FROM api_recent_requests WHERE route_template = ?",
            ("/health",),
        )["total"]

        response = client.get("/health")

        after = _fetch_one(
            "SELECT COUNT(*) AS total FROM api_recent_requests WHERE route_template = ?",
            ("/health",),
        )["total"]
        latest = _fetch_one(
            """
            SELECT method, route_template, status_code
            FROM api_recent_requests
            WHERE route_template = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            ("/health",),
        )

        assert response.status_code == 200
        assert after == before + 1
        assert latest["method"] == "GET"
        assert latest["route_template"] == "/health"
        assert latest["status_code"] == 200

    def test_request_updates_ip_and_route_counters(self, client):
        ip_before = _fetch_one(
            "SELECT request_count FROM api_ip_request_counts WHERE client_ip = ?",
            ("testclient",),
        )
        route_before = _fetch_one(
            "SELECT request_count FROM api_route_request_counts WHERE route_template = ?",
            ("/caen/{cod}",),
        )

        response = client.get("/caen/0111")

        ip_after = _fetch_one(
            "SELECT request_count FROM api_ip_request_counts WHERE client_ip = ?",
            ("testclient",),
        )
        route_after = _fetch_one(
            "SELECT request_count FROM api_route_request_counts WHERE route_template = ?",
            ("/caen/{cod}",),
        )
        assert response.status_code == 200
        assert ip_after["request_count"] == (ip_before["request_count"] if ip_before else 0) + 1
        assert route_after["request_count"] == (route_before["request_count"] if route_before else 0) + 1

    def test_recent_request_log_is_capped(self, client, monkeypatch):
        monkeypatch.setattr(auth, "_REQUEST_LOG_MAX_ROWS", 3)

        for _ in range(4):
            assert client.get("/health").status_code == 200

        total = _fetch_one("SELECT COUNT(*) AS total FROM api_recent_requests")["total"]
        assert total == 3


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
