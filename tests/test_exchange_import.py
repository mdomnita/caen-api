from email.utils import formatdate

from scripts import init_exchange_db


class _Response:
    def __init__(self, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_download_uses_cached_file_when_remote_not_modified(monkeypatch, tmp_path):
    monkeypatch.setattr(init_exchange_db, "TEMP_XML_DIR", tmp_path)

    cached = tmp_path / "nbrfxrates2026.xml"
    cached.write_bytes(b"cached-data")
    cached_mtime = 1_700_000_000
    cached.touch()
    monkeypatch.setattr(init_exchange_db.os.path, "getmtime", lambda _: cached_mtime, raising=False)

    calls = []

    def fake_get(url, timeout, verify, headers):
        calls.append((url, headers))
        return _Response(status_code=304)

    monkeypatch.setattr(init_exchange_db.requests, "get", fake_get)
    monkeypatch.setattr(init_exchange_db.email.utils, "formatdate", lambda value, usegmt: "Wed, 15 Nov 2023 10:13:20 GMT")

    result = init_exchange_db._download(2026)

    assert result == cached
    assert cached.read_bytes() == b"cached-data"
    assert calls == [
        (
            "https://curs.bnr.ro/files/xml/years/nbrfxrates2026.xml",
            {"If-Modified-Since": "Wed, 15 Nov 2023 10:13:20 GMT"},
        )
    ]


def test_download_overwrites_cached_file_when_remote_changed(monkeypatch, tmp_path):
    monkeypatch.setattr(init_exchange_db, "TEMP_XML_DIR", tmp_path)

    cached = tmp_path / "nbrfxrates2026.xml"
    cached.write_bytes(b"old-data")

    mtime_updates = []

    def fake_get(url, timeout, verify, headers):
        assert headers
        return _Response(
            status_code=200,
            content=b"new-data",
            headers={"Last-Modified": "Wed, 15 Nov 2023 10:13:20 GMT"},
        )

    monkeypatch.setattr(init_exchange_db.requests, "get", fake_get)
    monkeypatch.setattr(init_exchange_db.os, "utime", lambda path, times: mtime_updates.append((path, times)))

    result = init_exchange_db._download(2026)

    assert result == cached
    assert cached.read_bytes() == b"new-data"
    assert mtime_updates == [(cached, (1_700_043_200.0, 1_700_043_200.0))]


def test_download_fetches_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(init_exchange_db, "TEMP_XML_DIR", tmp_path)

    def fake_get(url, timeout, verify, headers):
        assert headers == {}
        return _Response(status_code=200, content=b"fresh-data")

    monkeypatch.setattr(init_exchange_db.requests, "get", fake_get)

    result = init_exchange_db._download(2025)

    assert result.read_bytes() == b"fresh-data"


def test_download_force_ignores_cache_and_refetches_unconditionally(monkeypatch, tmp_path):
    # Regression test: update_exchange_db.py relies on force=True for the
    # current year so a stale local file can never cause a 304 that hides
    # genuinely new rates (see its docstring + the loop that calls
    # _download(year, force=(year == current_year))).
    monkeypatch.setattr(init_exchange_db, "TEMP_XML_DIR", tmp_path)

    cached = tmp_path / "nbrfxrates2026.xml"
    cached.write_bytes(b"stale-cached-data")

    calls = []

    def fake_get(url, timeout, verify, headers):
        calls.append(headers)
        # even a server that would still say "not modified" against the
        # stale mtime must not be asked, since no If-Modified-Since is sent
        return _Response(status_code=200, content=b"fresh-data")

    monkeypatch.setattr(init_exchange_db.requests, "get", fake_get)

    result = init_exchange_db._download(2026, force=True)

    assert calls == [{}]  # no If-Modified-Since sent
    assert result.read_bytes() == b"fresh-data"


def test_download_force_never_returns_304_cached_path(monkeypatch, tmp_path):
    monkeypatch.setattr(init_exchange_db, "TEMP_XML_DIR", tmp_path)

    cached = tmp_path / "nbrfxrates2026.xml"
    cached.write_bytes(b"stale-cached-data")

    def fake_get(url, timeout, verify, headers):
        # a misbehaving/proxying server returning 304 despite no
        # If-Modified-Since must still not short-circuit force=True
        return _Response(status_code=304)

    monkeypatch.setattr(init_exchange_db.requests, "get", fake_get)

    result = init_exchange_db._download(2026, force=True)

    # falls through to raise_for_status()/write_bytes() with an empty body,
    # proving the early "return cached dest" branch was not taken
    assert result.read_bytes() == b""


def test_parse_matches_real_bnr_namespace(tmp_path):
    # Regression test: BNR_NS must be the XML's actual xmlns= ("https://
    # www.bnr.ro/xsd"), not its xsi:schemaLocation ("https://curs.bnr.ro/
    # xsd/nbrfxrates.xsd") -- using the schemaLocation URL makes findall()
    # match zero <Cube> elements, so _parse() silently returns [] for every
    # real file regardless of its actual content (update_exchange_db.py
    # then reports "no new data" even when the fetched file has new rates).
    xml_path = tmp_path / "nbrfxrates2026.xml"
    xml_path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<DataSet xmlns="https://www.bnr.ro/xsd" xmlns:xsi="https://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="https://curs.bnr.ro/xsd/nbrfxrates.xsd">'
        '<Header><Publisher>National Bank of Romania</Publisher></Header>'
        '<Body><Cube date="2026-08-07"><Rate currency="EUR">5.05</Rate></Cube></Body>'
        "</DataSet>",
        encoding="utf-8",
    )

    rows = init_exchange_db._parse(xml_path)

    assert rows == [("2026-08-07", "EUR", 5.05, 1)]