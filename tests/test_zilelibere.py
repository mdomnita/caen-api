"""Tests for /zilelibere endpoints."""


class TestZileLibere:
    def test_returns_all_holidays(self, client):
        r = client.get("/zilelibere")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 7

    def test_results_are_ordered_by_date_then_name(self, client):
        body = client.get("/zilelibere").json()
        assert [item["data"] for item in body] == [
            "2026-01-01",
            "2026-01-02",
            "2026-01-06",
            "2026-01-07",
            "2026-06-01",
            "2026-06-01",
            "2026-12-26",
        ]
        assert [item["denumire_sarbatoare"] for item in body[4:6]] == [
            "Rusalii - a doua zi",
            "Ziua Copilului",
        ]

    def test_filters_by_period(self, client):
        r = client.get("/zilelibere", params={"start": "2026-06-01", "end": "2026-06-30"})
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 2
        assert {item["denumire_sarbatoare"] for item in body} == {
            "Rusalii - a doua zi",
            "Ziua Copilului",
        }

    def test_accepts_single_sided_filters(self, client):
        r = client.get("/zilelibere", params={"start": "2026-12-01"})
        assert r.status_code == 200
        assert [item["data"] for item in r.json()] == ["2026-12-26"]

    def test_returns_boolean_for_weekend_flag(self, client):
        body = client.get("/zilelibere", params={"start": "2026-12-26", "end": "2026-12-26"}).json()
        assert body[0]["cade_in_weekend"] is True

    def test_invalid_period_returns_422(self, client):
        r = client.get("/zilelibere", params={"start": "2026-12-31", "end": "2026-01-01"})
        assert r.status_code == 422

    def test_invalid_date_returns_422(self, client):
        assert client.get("/zilelibere", params={"start": "nu-e-data"}).status_code == 422

    def test_has_cache_headers(self, client):
        r = client.get("/zilelibere")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_matching_etag_returns_304(self, client):
        r1 = client.get("/zilelibere")
        r2 = client.get("/zilelibere", headers={"If-None-Match": r1.headers["etag"]})
        assert r2.status_code == 304

    def test_invalid_key_returns_403(self, client):
        assert client.get("/zilelibere", headers={"X-API-KEY": "bad-key"}).status_code == 403


class TestZileLiberePeLuna:
    def test_returns_holidays_for_month(self, client):
        r = client.get("/zilelibere/luna/1")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 4
        assert {item["data"] for item in body} == {
            "2026-01-01",
            "2026-01-02",
            "2026-01-06",
            "2026-01-07",
        }

    def test_empty_month_returns_empty_list(self, client):
        r = client.get("/zilelibere/luna/2")
        assert r.status_code == 200
        assert r.json() == []

    def test_invalid_month_returns_422(self, client):
        assert client.get("/zilelibere/luna/13").status_code == 422

    def test_has_cache_headers(self, client):
        r = client.get("/zilelibere/luna/1")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers


class TestPunti:
    def test_returns_bridge_day_recommendations(self, client):
        r = client.get("/zilelibere/punti")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 2
        assert body[0] == {
            "interval_start": "2026-01-01",
            "interval_end": "2026-01-07",
            "zile_libere_totale": 7,
            "zile_concediu_necesare": 1,
            "zile_concediu": ["2026-01-05"],
            "zile_libere_legale": ["2026-01-01", "2026-01-02", "2026-01-06", "2026-01-07"],
        }

    def test_respects_max_zile_concediu(self, client):
        r = client.get("/zilelibere/punti", params={"max_zile_concediu": 1})
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["zile_concediu"] == ["2026-01-05"]

    def test_respects_min_zile_libere(self, client):
        r = client.get("/zilelibere/punti", params={"min_zile_libere": 6})
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 2
        assert body[0]["interval_start"] == "2026-01-01"
        assert body[0]["interval_end"] == "2026-01-07"
        assert body[1]["interval_start"] == "2026-01-06"
        assert body[1]["interval_end"] == "2026-01-11"

    def test_invalid_query_returns_422(self, client):
        assert client.get("/zilelibere/punti", params={"max_zile_concediu": 0}).status_code == 422

    def test_has_cache_headers(self, client):
        r = client.get("/zilelibere/punti")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers