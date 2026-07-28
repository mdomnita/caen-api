"""Tests for /schimb (BNR exchange rates) endpoints.

Seeded data (see conftest.py):
  EUR mult=1:   2025-01-02=5.0000, 2025-01-03=5.0100, 2025-01-06=5.0200
  USD mult=1:   2025-01-02=4.8000, 2025-01-03=4.8100, 2025-01-06=4.8200
  HUF mult=100: 2025-01-02=1.2000, 2025-01-03=1.2100
"""
from datetime import date, timedelta

import pytest


class TestValute:
    def test_returns_all_currencies(self, client):
        r = client.get("/schimb/valute")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 3

    def test_fields(self, client):
        assert set(client.get("/schimb/valute").json()[0].keys()) == {"valuta", "ultima_data", "curs_unitar"}

    def test_ordered_by_valuta(self, client):
        names = [v["valuta"] for v in client.get("/schimb/valute").json()]
        assert names == sorted(names)

    def test_curs_unitar_accounts_for_multiplicator(self, client):
        huf = next(v for v in client.get("/schimb/valute").json() if v["valuta"] == "HUF")
        # latest HUF: curs=1.2100, mult=100 → curs_unitar = 0.0121
        assert huf["curs_unitar"] == pytest.approx(0.0121, rel=1e-5)

    def test_eur_uses_latest_date(self, client):
        eur = next(v for v in client.get("/schimb/valute").json() if v["valuta"] == "EUR")
        assert eur["ultima_data"] == "2025-01-06"
        assert eur["curs_unitar"] == pytest.approx(5.0200, rel=1e-5)

    def test_has_cache_headers(self, client):
        r = client.get("/schimb/valute")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_invalid_key_returns_403(self, client):
        assert client.get("/schimb/valute", headers={"X-API-KEY": "bad-key"}).status_code == 403


class TestValuteLaData:
    def test_returns_all_currencies_for_exact_date(self, client):
        r = client.get("/schimb/valute/2025-01-03")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 3

    def test_falls_back_to_prior_trading_day_per_currency(self, client):
        r = client.get("/schimb/valute/2025-01-06")
        assert r.status_code == 200
        body = r.json()
        huf = next(v for v in body if v["valuta"] == "HUF")
        assert huf["data"] == "2025-01-03"
        assert huf["curs_unitar"] == pytest.approx(1.2100 / 100, rel=1e-5)

    def test_fields(self, client):
        assert set(client.get("/schimb/valute/2025-01-03").json()[0].keys()) == {
            "data", "valuta", "curs", "multiplicator", "curs_unitar"
        }

    def test_ordered_by_valuta(self, client):
        names = [v["valuta"] for v in client.get("/schimb/valute/2025-01-03").json()]
        assert names == sorted(names)

    def test_date_before_all_data_returns_404(self, client):
        assert client.get("/schimb/valute/2000-01-01").status_code == 404

    def test_invalid_date_returns_422(self, client):
        assert client.get("/schimb/valute/not-a-date").status_code == 422

    def test_future_date_returns_422(self, client):
        future_date = (date.today() + timedelta(days=1)).isoformat()
        assert client.get(f"/schimb/valute/{future_date}").status_code == 422

    def test_has_cache_headers(self, client):
        r = client.get("/schimb/valute/2025-01-03")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers


class TestCurs:
    def test_known_date_returns_rate(self, client):
        r = client.get("/schimb/curs/EUR/2025-01-03")
        assert r.status_code == 200
        body = r.json()
        assert body["data"] == "2025-01-03"
        assert body["valuta"] == "EUR"
        assert body["curs"] == pytest.approx(5.0100, rel=1e-5)
        assert body["multiplicator"] == 1
        assert body["curs_unitar"] == pytest.approx(5.0100, rel=1e-5)

    def test_falls_back_to_prior_trading_day(self, client):
        # 2025-01-04 and 2025-01-05 are weekend → nearest prior is 2025-01-03
        r = client.get("/schimb/curs/EUR/2025-01-05")
        assert r.status_code == 200
        assert r.json()["data"] == "2025-01-03"

    def test_valuta_is_uppercased(self, client):
        r = client.get("/schimb/curs/eur/2025-01-06")
        assert r.status_code == 200
        assert r.json()["valuta"] == "EUR"

    def test_unknown_valuta_returns_404(self, client):
        assert client.get("/schimb/curs/XYZ/2025-01-06").status_code == 404

    def test_date_before_all_data_returns_404(self, client):
        assert client.get("/schimb/curs/EUR/2000-01-01").status_code == 404

    def test_invalid_date_returns_422(self, client):
        assert client.get("/schimb/curs/EUR/not-a-date").status_code == 422

    def test_future_date_returns_422(self, client):
        future_date = (date.today() + timedelta(days=1)).isoformat()
        assert client.get(f"/schimb/curs/EUR/{future_date}").status_code == 422

    def test_huf_curs_unitar_uses_multiplicator(self, client):
        r = client.get("/schimb/curs/HUF/2025-01-02")
        assert r.status_code == 200
        body = r.json()
        assert body["multiplicator"] == 100
        assert body["curs_unitar"] == pytest.approx(1.2000 / 100, rel=1e-5)

    def test_fields(self, client):
        assert set(client.get("/schimb/curs/EUR/2025-01-06").json().keys()) == {
            "data", "valuta", "curs", "multiplicator", "curs_unitar"
        }

    def test_has_cache_headers(self, client):
        r = client.get("/schimb/curs/EUR/2025-01-06")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_matching_etag_returns_304(self, client):
        r1 = client.get("/schimb/curs/EUR/2025-01-06")
        r2 = client.get("/schimb/curs/EUR/2025-01-06", headers={"If-None-Match": r1.headers["etag"]})
        assert r2.status_code == 304


class TestEvolutie:
    def test_returns_points_in_range(self, client):
        r = client.get("/schimb/evolutie/EUR", params={"start": "2025-01-02", "end": "2025-01-06"})
        assert r.status_code == 200
        body = r.json()
        assert body["sursa"] == "EUR"
        assert body["destinatie"] == "RON"
        assert body["date_start"] == "2025-01-02"
        assert body["date_end"] == "2025-01-06"
        assert len(body["puncte"]) == 3

    def test_puncte_ordered_by_date(self, client):
        body = client.get("/schimb/evolutie/EUR", params={"start": "2025-01-02", "end": "2025-01-06"}).json()
        dates = [p["data"] for p in body["puncte"]]
        assert dates == sorted(dates)

    def test_puncte_fields(self, client):
        body = client.get("/schimb/evolutie/EUR", params={"start": "2025-01-02", "end": "2025-01-06"}).json()
        assert set(body["puncte"][0].keys()) == {"data", "curs"}

    def test_curs_is_curs_unitar(self, client):
        body = client.get("/schimb/evolutie/EUR", params={"start": "2025-01-02", "end": "2025-01-02"}).json()
        assert body["puncte"][0]["curs"] == pytest.approx(5.0000, rel=1e-5)

    def test_end_defaults_to_today(self, client):
        # All seeded EUR data is in 2025; omitting end returns all 3 records
        r = client.get("/schimb/evolutie/EUR", params={"start": "2025-01-01"})
        assert r.status_code == 200
        assert len(r.json()["puncte"]) == 3

    def test_no_data_in_range_returns_404(self, client):
        r = client.get("/schimb/evolutie/EUR", params={"start": "2024-01-01", "end": "2024-12-31"})
        assert r.status_code == 404

    def test_unknown_valuta_returns_404(self, client):
        r = client.get("/schimb/evolutie/XYZ", params={"start": "2025-01-01", "end": "2025-12-31"})
        assert r.status_code == 404

    def test_invalid_dates_return_422(self, client):
        r = client.get("/schimb/evolutie/EUR", params={"start": "not-a-date", "end": "zzzz"})
        assert r.status_code == 422

    def test_future_dates_return_422(self, client):
        future_date = (date.today() + timedelta(days=1)).isoformat()
        r = client.get("/schimb/evolutie/EUR", params={"start": future_date, "end": future_date})
        assert r.status_code == 422

    def test_fields(self, client):
        body = client.get("/schimb/evolutie/EUR", params={"start": "2025-01-02", "end": "2025-01-06"}).json()
        assert set(body.keys()) == {"sursa", "destinatie", "date_start", "date_end", "puncte"}

    def test_has_cache_headers(self, client):
        r = client.get("/schimb/evolutie/EUR", params={"start": "2025-01-02", "end": "2025-01-06"})
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers


class TestPereche:
    def test_cross_rate(self, client):
        r = client.get("/schimb/pereche/EUR/USD/2025-01-06")
        assert r.status_code == 200
        body = r.json()
        assert body["sursa"] == "EUR"
        assert body["destinatie"] == "USD"
        assert body["curs"] == pytest.approx(5.0200 / 4.8200, rel=1e-5)

    def test_same_currency_returns_1(self, client):
        r = client.get("/schimb/pereche/EUR/EUR/2025-01-06")
        assert r.status_code == 200
        assert r.json()["curs"] == 1.0

    def test_ron_as_source(self, client):
        # RON/EUR = mult / curs = 1 / 5.0200
        r = client.get("/schimb/pereche/RON/EUR/2025-01-06")
        assert r.status_code == 200
        assert r.json()["curs"] == pytest.approx(round(1 / 5.0200,4), rel=1e-5)

    def test_ron_as_destination(self, client):
        # EUR/RON = curs / mult = 5.0200
        r = client.get("/schimb/pereche/EUR/RON/2025-01-06")
        assert r.status_code == 200
        assert r.json()["curs"] == pytest.approx(5.0200, rel=1e-5)

    def test_fallback_to_prior_trading_day(self, client):
        # 2025-01-05 is Sunday; both EUR and USD fall back to 2025-01-03
        r = client.get("/schimb/pereche/EUR/USD/2025-01-05")
        assert r.status_code == 200
        assert r.json()["data"] == "2025-01-03"

    def test_unknown_source_returns_404(self, client):
        assert client.get("/schimb/pereche/XYZ/USD/2025-01-06").status_code == 404

    def test_unknown_destination_returns_404(self, client):
        assert client.get("/schimb/pereche/EUR/XYZ/2025-01-06").status_code == 404

    def test_invalid_date_returns_422(self, client):
        assert client.get("/schimb/pereche/EUR/USD/not-a-date").status_code == 422

    def test_future_date_returns_422(self, client):
        future_date = (date.today() + timedelta(days=1)).isoformat()
        assert client.get(f"/schimb/pereche/EUR/USD/{future_date}").status_code == 422

    def test_valuta_is_uppercased(self, client):
        r = client.get("/schimb/pereche/eur/usd/2025-01-06")
        assert r.status_code == 200
        body = r.json()
        assert body["sursa"] == "EUR"
        assert body["destinatie"] == "USD"

    def test_fields(self, client):
        assert set(client.get("/schimb/pereche/EUR/USD/2025-01-06").json().keys()) == {
            "data", "sursa", "destinatie", "curs"
        }

    def test_has_cache_headers(self, client):
        r = client.get("/schimb/pereche/EUR/USD/2025-01-06")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers


class TestEvolutiePereche:
    def test_cross_rate_time_series(self, client):
        r = client.get("/schimb/evolutie/pereche/EUR/USD", params={"start": "2025-01-02", "end": "2025-01-06"})
        assert r.status_code == 200
        body = r.json()
        assert body["sursa"] == "EUR"
        assert body["destinatie"] == "USD"
        assert len(body["puncte"]) == 3  # EUR and USD share all 3 dates

    def test_ron_as_source(self, client):
        r = client.get("/schimb/evolutie/pereche/RON/EUR", params={"start": "2025-01-02", "end": "2025-01-06"})
        assert r.status_code == 200
        body = r.json()
        assert body["sursa"] == "RON"
        assert body["destinatie"] == "EUR"
        assert len(body["puncte"]) == 3
        first = next(p for p in body["puncte"] if p["data"] == "2025-01-02")
        assert first["curs"] == pytest.approx(1 / 5.0000, rel=1e-5)

    def test_ron_as_destination(self, client):
        r = client.get("/schimb/evolutie/pereche/EUR/RON", params={"start": "2025-01-02", "end": "2025-01-06"})
        assert r.status_code == 200
        body = r.json()
        assert len(body["puncte"]) == 3
        first = next(p for p in body["puncte"] if p["data"] == "2025-01-02")
        assert first["curs"] == pytest.approx(5.0000, rel=1e-5)

    def test_inner_join_drops_dates_missing_from_one_series(self, client):
        # HUF only has data on 2025-01-02 and 2025-01-03; EUR has all 3 dates
        # The JOIN yields only the 2 shared dates
        r = client.get("/schimb/evolutie/pereche/EUR/HUF", params={"start": "2025-01-02", "end": "2025-01-06"})
        assert r.status_code == 200
        assert len(r.json()["puncte"]) == 2

    def test_end_defaults_to_today(self, client):
        r = client.get("/schimb/evolutie/pereche/EUR/USD", params={"start": "2025-01-01"})
        assert r.status_code == 200
        assert len(r.json()["puncte"]) == 3

    def test_no_data_returns_404(self, client):
        r = client.get("/schimb/evolutie/pereche/EUR/USD", params={"start": "2024-01-01", "end": "2024-12-31"})
        assert r.status_code == 404

    def test_unknown_valuta_returns_404(self, client):
        r = client.get("/schimb/evolutie/pereche/XYZ/USD", params={"start": "2025-01-01", "end": "2025-12-31"})
        assert r.status_code == 404

    def test_invalid_dates_return_422(self, client):
        r = client.get("/schimb/evolutie/pereche/EUR/USD", params={"start": "not-a-date", "end": "zzzz"})
        assert r.status_code == 422

    def test_future_dates_return_422(self, client):
        future_date = (date.today() + timedelta(days=1)).isoformat()
        r = client.get("/schimb/evolutie/pereche/EUR/USD", params={"start": future_date, "end": future_date})
        assert r.status_code == 422

    def test_fields(self, client):
        body = client.get("/schimb/evolutie/pereche/EUR/USD", params={"start": "2025-01-02", "end": "2025-01-06"}).json()
        assert set(body.keys()) == {"sursa", "destinatie", "date_start", "date_end", "puncte"}
        assert set(body["puncte"][0].keys()) == {"data", "curs"}

    def test_has_cache_headers(self, client):
        r = client.get("/schimb/evolutie/pereche/EUR/USD", params={"start": "2025-01-02", "end": "2025-01-06"})
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers
