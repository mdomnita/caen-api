"""Tests for /siruta endpoints.

Seeded localities (see conftest.py):
  cod 666 FOCSANI  tip_cod=12 (Municipiu) judet=41 VRANCEA
  cod 667 ADJUD    tip_cod=13 (Oras)      judet=41 VRANCEA
  cod 668 PANCIU   tip_cod=14 (Comuna)    judet=41 VRANCEA
  cod 100 BRASOV   tip_cod=12 (Municipiu) judet=10 BRASOV

Seeded judete/regiuni/componente (NOU):
  judet 10 BRASOV  abbr=BV cod_regiune=7 (Centru)   cod_siruta_judet=65
  judet 41 VRANCEA abbr=VN cod_regiune=2 (Sud-Est)  cod_siruta_judet=396
  localitati_componente 669 GOLESTI, 670 MANDRESTI -- sate ale FOCSANI (666)
  localitati_componente 671 PARVU (denumire_ascii) -- sat al PANCIU (668)
  coduri_postale 620100 -> cod_siruta=669 (GOLESTI)
"""


class TestJudete:
    def test_returns_all_counties(self, client):
        r = client.get("/siruta/judete")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 2

    def test_fields(self, client):
        assert set(client.get("/siruta/judete").json()[0].keys()) == {"cod_judet", "denumire"}

    def test_ordered_by_name(self, client):
        names = [j["denumire"] for j in client.get("/siruta/judete").json()]
        assert names == sorted(names)

    def test_has_cache_headers(self, client):
        r = client.get("/siruta/judete")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_matching_etag_returns_304(self, client):
        r1 = client.get("/siruta/judete")
        r2 = client.get("/siruta/judete", headers={"If-None-Match": r1.headers["etag"]})
        assert r2.status_code == 304

    def test_invalid_key_returns_403(self, client):
        assert client.get("/siruta/judete", headers={"X-API-KEY": "bad-key"}).status_code == 403


class TestLocalitate:
    def test_known_code_returns_entry(self, client):
        r = client.get("/siruta/localitate/666")
        assert r.status_code == 200
        body = r.json()
        assert body["cod_siruta"] == 666
        assert body["denumire"] == "FOCSANI"
        assert body["tip_cod"] == 12
        assert body["tip_abrev"] == "Mun."
        assert body["tip_denumire"] == "Municipiu"
        assert body["cod_judet"] == 41
        assert body["judet_denumire"] == "VRANCEA"

    def test_diacritics_field_included_in_response(self, client):
        # cached_json bypasses Pydantic, so denumire_diacritice is included raw
        body = client.get("/siruta/localitate/666").json()
        assert "denumire_diacritice" in body
        assert body["denumire_diacritice"] == "FOCŞANI"

    def test_uat_result_has_nivel_and_no_parinte(self, client):
        body = client.get("/siruta/localitate/666").json()
        assert body["nivel"] == "UAT"
        assert body["cod_siruta_parinte"] is None

    def test_falls_back_to_sat_when_not_a_uat(self, client):
        # 669 GOLESTI e doar in localitati_componente, nu in localitati
        r = client.get("/siruta/localitate/669")
        assert r.status_code == 200
        body = r.json()
        assert body["cod_siruta"] == 669
        assert body["denumire"] == "GOLESTI"
        assert body["nivel"] == "componenta"
        assert body["cod_siruta_parinte"] == 666
        assert body["tip_abrev"] is None
        assert body["judet_denumire"] == "VRANCEA"

    def test_unknown_code_returns_404(self, client):
        assert client.get("/siruta/localitate/99999").status_code == 404

    def test_404_detail_contains_code(self, client):
        assert "99999" in client.get("/siruta/localitate/99999").json()["detail"]

    def test_has_cache_headers(self, client):
        r = client.get("/siruta/localitate/666")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_matching_etag_returns_304(self, client):
        r1 = client.get("/siruta/localitate/666")
        r2 = client.get("/siruta/localitate/666", headers={"If-None-Match": r1.headers["etag"]})
        assert r2.status_code == 304


class TestCautare:
    def test_ascii_name_match(self, client):
        r = client.get("/siruta/cautare", params={"q": "FOCSANI"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["results"][0]["cod_siruta"] == 666

    def test_partial_name_match(self, client):
        r = client.get("/siruta/cautare", params={"q": "ADJ"})
        assert r.status_code == 200
        assert r.json()["results"][0]["cod_siruta"] == 667

    def test_no_match_returns_empty(self, client):
        body = client.get("/siruta/cautare", params={"q": "ZZNOTFOUND"}).json()
        assert body["total"] == 0
        assert body["results"] == []

    def test_q_too_short_returns_422(self, client):
        assert client.get("/siruta/cautare", params={"q": "F"}).status_code == 422

    def test_pagination_limit(self, client):
        # q="AN" matches FOCSANI, PANCIU (UAT) si MANDRESTI (componenta)
        body = client.get("/siruta/cautare", params={"q": "AN", "limit": 1}).json()
        assert body["total"] == 3
        assert len(body["results"]) == 1

    def test_pagination_offset(self, client):
        r1 = client.get("/siruta/cautare", params={"q": "AN", "limit": 1, "offset": 0})
        r2 = client.get("/siruta/cautare", params={"q": "AN", "limit": 1, "offset": 1})
        assert r1.json()["total"] == r2.json()["total"] == 3
        assert r1.json()["results"][0]["cod_siruta"] != r2.json()["results"][0]["cod_siruta"]

    def test_results_have_expected_fields(self, client):
        entry = client.get("/siruta/cautare", params={"q": "FOCSANI"}).json()["results"][0]
        for field in (
            "cod_siruta", "denumire", "tip_cod", "tip_abrev", "tip_denumire",
            "cod_judet", "judet_denumire", "nivel", "cod_siruta_parinte",
        ):
            assert field in entry

    def test_uat_result_has_no_parinte_but_has_abrev(self, client):
        entry = client.get("/siruta/cautare", params={"q": "FOCSANI"}).json()["results"][0]
        assert entry["nivel"] == "UAT"
        assert entry["tip_abrev"] == "Mun."
        assert entry["cod_siruta_parinte"] is None

    def test_finds_sat_by_name_ascii(self, client):
        r = client.get("/siruta/cautare", params={"q": "GOLESTI"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        entry = body["results"][0]
        assert entry["cod_siruta"] == 669
        assert entry["nivel"] == "componenta"
        assert entry["cod_siruta_parinte"] == 666
        assert entry["tip_abrev"] is None

    def test_finds_sat_by_name_with_diacritics(self, client):
        # cauta "parvu" (fara diacritice) impotriva denumire_ascii="PARVU" din sat cu diacritice "PÂRVU"
        r = client.get("/siruta/cautare", params={"q": "parvu"})
        body = r.json()
        assert body["total"] == 1
        assert body["results"][0]["cod_siruta"] == 671
        assert body["results"][0]["denumire_diacritice"] == "PÂRVU"

    def test_query_matching_both_levels_returns_combined_ordered_results(self, client):
        # "AN" matches FOCSANI, PANCIU (UAT) si MANDRESTI (componenta), ordonate alfabetic
        body = client.get("/siruta/cautare", params={"q": "AN"}).json()
        denumiri = [row["denumire"] for row in body["results"]]
        assert denumiri == sorted(denumiri)
        nivele = {row["nivel"] for row in body["results"]}
        assert nivele == {"UAT", "componenta"}

    def test_has_cache_headers(self, client):
        r = client.get("/siruta/cautare", params={"q": "FOCSANI"})
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers


class TestJudetLocalitati:
    def test_returns_all_localities_in_county(self, client):
        r = client.get("/siruta/judet/41")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 3
        codes = {e["cod_siruta"] for e in body}
        assert codes == {666, 667, 668}

    def test_ordered_by_tip_cod_then_name(self, client):
        body = client.get("/siruta/judet/41").json()
        assert body[0]["cod_siruta"] == 666  # Municipiu, tip_cod=12
        assert body[1]["cod_siruta"] == 667  # Oras, tip_cod=13
        assert body[2]["cod_siruta"] == 668  # Comuna, tip_cod=14

    def test_tip_cod_filter(self, client):
        r = client.get("/siruta/judet/41", params={"tip_cod": 12})
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["cod_siruta"] == 666

    def test_unknown_county_returns_empty_list(self, client):
        r = client.get("/siruta/judet/99")
        assert r.status_code == 200
        assert r.json() == []

    def test_different_counties_return_different_results(self, client):
        vrancea = {e["cod_siruta"] for e in client.get("/siruta/judet/41").json()}
        brasov = {e["cod_siruta"] for e in client.get("/siruta/judet/10").json()}
        assert vrancea.isdisjoint(brasov)

    def test_has_cache_headers(self, client):
        r = client.get("/siruta/judet/41")
        assert "public" in r.headers.get("cache-control", "")
        assert "etag" in r.headers

    def test_matching_etag_returns_304(self, client):
        r1 = client.get("/siruta/judet/41")
        r2 = client.get("/siruta/judet/41", headers={"If-None-Match": r1.headers["etag"]})
        assert r2.status_code == 304


# NOU: teste pentru GET /siruta/judete/{cod_judet} si /siruta/judete/abbr/{abbr}
class TestJudetDetail:
    def test_by_cod_judet(self, client):
        r = client.get("/siruta/judete/10")
        assert r.status_code == 200
        body = r.json()
        assert body == {
            "cod_judet": 10,
            "denumire": "BRASOV",
            "abbr": "BV",
            "cod_regiune": 7,
            "regiune_denumire": "Centru",
            "cod_siruta_judet": 65,
        }

    def test_unknown_cod_judet_returns_404(self, client):
        assert client.get("/siruta/judete/999").status_code == 404

    def test_by_abbr(self, client):
        r = client.get("/siruta/judete/abbr/VN")
        assert r.status_code == 200
        body = r.json()
        assert body["cod_judet"] == 41
        assert body["denumire"] == "VRANCEA"
        assert body["cod_regiune"] == 2
        assert body["regiune_denumire"] == "Sud-Est"

    def test_abbr_is_case_insensitive(self, client):
        r = client.get("/siruta/judete/abbr/vn")
        assert r.status_code == 200
        assert r.json()["cod_judet"] == 41

    def test_unknown_abbr_returns_404(self, client):
        assert client.get("/siruta/judete/abbr/ZZ").status_code == 404


# NOU: teste pentru GET /siruta/regiuni si /siruta/regiuni/{cod}/judete
class TestRegiuni:
    def test_lists_all_regions_ordered_by_name(self, client):
        r = client.get("/siruta/regiuni")
        assert r.status_code == 200
        body = r.json()
        assert [row["denumire"] for row in body] == ["Centru", "Sud-Est"]

    def test_region_fields(self, client):
        row = client.get("/siruta/regiuni").json()[0]
        assert row == {"cod_regiune": 7, "denumire": "Centru", "nuts2": "RO12"}

    def test_judete_in_region(self, client):
        r = client.get("/siruta/regiuni/7/judete")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["cod_judet"] == 10

    def test_unknown_region_returns_404(self, client):
        assert client.get("/siruta/regiuni/999/judete").status_code == 404


# NOU: teste pentru GET /siruta/localitate/{cod}/componente
class TestComponente:
    def test_returns_component_localities_of_a_uat(self, client):
        r = client.get("/siruta/localitate/666/componente")
        assert r.status_code == 200
        body = r.json()
        assert body["parinte"]["cod_siruta"] == 666
        assert body["total"] == 2
        cods = [row["cod_siruta"] for row in body["results"]]
        assert cods == [669, 670]  # acelasi tip_cod -- ordonate alfabetic (GOLESTI < MANDRESTI)

    def test_postal_codes_joined_where_available(self, client):
        body = client.get("/siruta/localitate/666/componente").json()
        by_cod = {row["cod_siruta"]: row for row in body["results"]}
        assert by_cod[669]["coduri_postale"] == ["620100"]
        assert by_cod[670]["coduri_postale"] == []  # nicio potrivire in coduri_postale

    def test_tip_denumire_included(self, client):
        body = client.get("/siruta/localitate/666/componente").json()
        assert body["results"][0]["tip_denumire"] == "Sat aparținător municipiu reședință de județ"

    def test_uat_with_no_components_returns_404(self, client):
        assert client.get("/siruta/localitate/667/componente").status_code == 404

    def test_unknown_parent_code_returns_404(self, client):
        assert client.get("/siruta/localitate/99999/componente").status_code == 404


# NOU: teste pentru GET /siruta/tipuri
class TestTipuri:
    def test_includes_uat_and_componenta_levels(self, client):
        r = client.get("/siruta/tipuri")
        assert r.status_code == 200
        body = r.json()
        by_key = {(row["tip_cod"], row["nivel"]): row["tip_denumire"] for row in body}
        assert by_key[(12, "UAT")] == "Municipiu"
        assert by_key[(23, "componenta")] == "Sat aparținător comună"

    def test_no_duplicate_tip_cod_within_same_nivel(self, client):
        body = client.get("/siruta/tipuri").json()
        keys = [(row["tip_cod"], row["nivel"]) for row in body]
        assert len(keys) == len(set(keys))
