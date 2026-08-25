"""Tests for /localitati endpoints.

Seeded localitati_geo (see conftest.py), all with an entry in localitati_geo_rtree:
  gid 1 Focsani       lat=45.6967 lon=27.1858 judet=Vrancea   (cod_siruta=666)
  gid 2 Independenta  lat=44.2833 lon=27.7000 judet=Constanta (cod_siruta=None)
  gid 3 Independenta  lat=45.7333 lon=27.9333 judet=Galati    (cod_siruta=None)
"""


class TestNearby:
    def test_finds_point_itself_at_zero_distance(self, client):
        r = client.get("/localitati/nearby", params={"lat": 45.6967, "lon": 27.1858, "radius_km": 1})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["results"][0]["gid"] == 1
        assert body["results"][0]["distanta_km"] == 0.0

    def test_small_radius_excludes_farther_point(self, client):
        # Focsani (gid 1) and Galati-area point (gid 3) are ~80km+ apart in this fixture.
        r = client.get("/localitati/nearby", params={"lat": 45.6967, "lon": 27.1858, "radius_km": 5})
        gids = {row["gid"] for row in r.json()["results"]}
        assert gids == {1}

    def test_large_radius_includes_farther_point(self, client):
        r = client.get("/localitati/nearby", params={"lat": 45.6967, "lon": 27.1858, "radius_km": 200})
        gids = {row["gid"] for row in r.json()["results"]}
        assert gids == {1, 2, 3}

    def test_results_ordered_by_distance_ascending(self, client):
        r = client.get("/localitati/nearby", params={"lat": 45.6967, "lon": 27.1858, "radius_km": 200})
        distances = [row["distanta_km"] for row in r.json()["results"]]
        assert distances == sorted(distances)

    def test_empty_area_returns_empty_list_not_404(self, client):
        r = client.get("/localitati/nearby", params={"lat": 0, "lon": 0, "radius_km": 10})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 0
        assert body["results"] == []

    def test_limit_caps_results(self, client):
        r = client.get(
            "/localitati/nearby", params={"lat": 45.6967, "lon": 27.1858, "radius_km": 200, "limit": 1}
        )
        assert len(r.json()["results"]) == 1

    def test_echoes_request_params(self, client):
        r = client.get("/localitati/nearby", params={"lat": 45.6967, "lon": 27.1858, "radius_km": 5})
        body = r.json()
        assert body["lat"] == 45.6967
        assert body["lon"] == 27.1858
        assert body["radius_km"] == 5.0

    def test_missing_required_params_returns_422(self, client):
        assert client.get("/localitati/nearby", params={"lat": 45.6967}).status_code == 422

    def test_out_of_range_lat_returns_422(self, client):
        r = client.get("/localitati/nearby", params={"lat": 100, "lon": 27.1858, "radius_km": 5})
        assert r.status_code == 422

    def test_negative_radius_returns_422(self, client):
        r = client.get("/localitati/nearby", params={"lat": 45.6967, "lon": 27.1858, "radius_km": -1})
        assert r.status_code == 422

    def test_has_cache_headers(self, client):
        r = client.get("/localitati/nearby", params={"lat": 45.6967, "lon": 27.1858, "radius_km": 5})
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers
