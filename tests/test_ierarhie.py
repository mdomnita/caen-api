"""Tests for /sectiuni, /diviziuni, /grupe hierarchy endpoints."""


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
        assert set(client.get("/sectiuni").json()[0].keys()) == {"cod", "denumire"}

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
        assert set(client.get("/sectiuni/A/diviziuni").json()[0].keys()) == {"cod", "denumire", "sectiune_cod"}

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
        assert set(client.get("/diviziuni/01/grupe").json()[0].keys()) == {"cod", "denumire", "diviziune_cod"}

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
        assert set(client.get("/grupe/011/clase").json()[0].keys()) == {
            "cod_caen", "denumire",
            "sectiune_cod", "sectiune",
            "diviziune_cod", "diviziune",
            "grupa_cod", "grupa",
        }

    def test_results_ordered_by_code(self, client):
        codes = [e["cod_caen"] for e in client.get("/grupe/011/clase").json()]
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
