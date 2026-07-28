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
            "https://www.bnr.ro/files/xml/years/nbrfxrates2026.xml",
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