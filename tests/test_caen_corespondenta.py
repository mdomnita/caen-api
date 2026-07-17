"""Tests for /caen/v2, /caen/v3/{cod}/v2 and /caen/corespondenta endpoints."""


class TestGetV2Detail:
    def test_known_code_returns_denumire_and_corespondente(self, client):
        r = client.get("/caen/v2/0113")
        assert r.status_code == 200
        body = r.json()
        assert body["cod"] == "0113"
        assert body["denumire"] == "Cultivarea legumelor v2"
        assert len(body["corespondente"]) == 2

    def test_corespondente_shape(self, client):
        body = client.get("/caen/v2/0113").json()
        codes = {c["cod_v3"]: c["tip_corespondenta"] for c in body["corespondente"]}
        assert codes == {"0111": "MIXT", "0112": "DETALIERE"}

    def test_single_correspondence(self, client):
        body = client.get("/caen/v2/0111").json()
        assert len(body["corespondente"]) == 1
        assert body["corespondente"][0]["cod_v3"] == "0111"
        assert body["corespondente"][0]["tip_corespondenta"] == "NESCHIMBAT"

    def test_unknown_code_returns_404(self, client):
        assert client.get("/caen/v2/9999").status_code == 404

    def test_404_detail_contains_the_code(self, client):
        assert "9999" in client.get("/caen/v2/9999").json()["detail"]


class TestGetV3Predecesori:
    def test_known_code_returns_predecesori(self, client):
        r = client.get("/caen/v3/0111/v2")
        assert r.status_code == 200
        body = r.json()
        assert body["cod"] == "0111"
        assert len(body["predecesori"]) == 2

    def test_predecesori_shape(self, client):
        body = client.get("/caen/v3/0111/v2").json()
        codes = {p["cod_v2"]: p["tip_corespondenta"] for p in body["predecesori"]}
        assert codes == {"0111": "NESCHIMBAT", "0113": "MIXT"}

    def test_nou_predecessor_has_null_cod_v2(self, client):
        body = client.get("/caen/v3/0112/v2").json()
        nou_items = [p for p in body["predecesori"] if p["tip_corespondenta"] == "NOU"]
        assert len(nou_items) == 1
        assert nou_items[0]["cod_v2"] is None
        assert nou_items[0]["denumire_v2"] is None

    def test_unknown_code_returns_404(self, client):
        assert client.get("/caen/v3/9999/v2").status_code == 404

    def test_404_detail_contains_the_code(self, client):
        assert "9999" in client.get("/caen/v3/9999/v2").json()["detail"]


class TestSearchCorespondenta:
    def test_filter_by_v3(self, client):
        body = client.get("/caen/corespondenta", params={"v3": "0111"}).json()
        assert body["total"] == 2

    def test_filter_by_v2(self, client):
        body = client.get("/caen/corespondenta", params={"v2": "0113"}).json()
        assert body["total"] == 2

    def test_filter_by_tip(self, client):
        body = client.get("/caen/corespondenta", params={"v3": "0112", "tip": "NOU"}).json()
        assert body["total"] == 1
        assert body["results"][0]["cod_v2"] is None

    def test_missing_v2_and_v3_returns_422(self, client):
        assert client.get("/caen/corespondenta").status_code == 422

    def test_invalid_tip_returns_422(self, client):
        r = client.get("/caen/corespondenta", params={"v3": "0111", "tip": "INVALID"})
        assert r.status_code == 422

    def test_pagination_limit(self, client):
        body = client.get("/caen/corespondenta", params={"v3": "0111", "limit": 1}).json()
        assert body["total"] == 2
        assert len(body["results"]) == 1


class TestApiKeyAuth:
    def test_invalid_key_returns_403_on_v2_detail(self, client):
        assert client.get("/caen/v2/0111", headers={"X-API-KEY": "bad-key"}).status_code == 403

    def test_invalid_key_returns_403_on_v3_predecesori(self, client):
        assert client.get("/caen/v3/0111/v2", headers={"X-API-KEY": "bad-key"}).status_code == 403

    def test_invalid_key_returns_403_on_search(self, client):
        assert client.get(
            "/caen/corespondenta", params={"v3": "0111"}, headers={"X-API-KEY": "bad-key"}
        ).status_code == 403


class TestCaching:
    def test_v2_detail_has_cache_control_and_etag(self, client):
        r = client.get("/caen/v2/0111")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_v3_predecesori_has_cache_control_and_etag(self, client):
        r = client.get("/caen/v3/0111/v2")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_search_has_cache_control_and_etag(self, client):
        r = client.get("/caen/corespondenta", params={"v3": "0111"})
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers
