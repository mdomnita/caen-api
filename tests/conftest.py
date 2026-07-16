"""
Pytest configuration and shared fixtures.

The temp DB is created and seeded at module load time — before pytest imports
any test file — so that SQLITE_DB is in the environment before main.py is first
imported (main.py reads SQLITE_DB at module level).
"""
import hashlib
import os
import secrets
import sqlite3
import tempfile

import sys

import pytest

# ── ensure project root is importable ────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# ── must happen before `from main import ...` ────────────────────────────────
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["SQLITE_DB"] = _tmp.name
# ─────────────────────────────────────────────────────────────────────────────

from starlette.testclient import TestClient  # noqa: E402
from main import app, limiter  # noqa: E402

# A stable test API key for the session
_VALID_API_KEY = "caen_sk_test_" + secrets.token_hex(16)


def _seed_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sectiuni (
            cod      TEXT PRIMARY KEY,
            denumire TEXT NOT NULL
        );
        CREATE TABLE diviziuni (
            cod          TEXT PRIMARY KEY,
            denumire     TEXT NOT NULL,
            sectiune_cod TEXT NOT NULL
        );
        CREATE TABLE grupe (
            cod           TEXT PRIMARY KEY,
            denumire      TEXT NOT NULL,
            diviziune_cod TEXT NOT NULL
        );
        CREATE TABLE clase (
            cod       TEXT PRIMARY KEY,
            denumire  TEXT NOT NULL,
            grupa_cod TEXT NOT NULL
        );
        CREATE INDEX idx_clase_denumire ON clase(denumire);
        CREATE TABLE api_keys (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash   TEXT    NOT NULL UNIQUE,
            name       TEXT    NOT NULL,
            created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
            is_active  INTEGER NOT NULL DEFAULT 1
        );
    """)
    conn.execute("INSERT INTO sectiuni VALUES ('A', 'Agricultura, silvicultura si pescuit')")
    conn.execute("INSERT INTO diviziuni VALUES ('01', 'Cultura vegetala si animala', 'A')")
    conn.execute("INSERT INTO grupe VALUES ('011', 'Cultura plantelor nepermanente', '01')")
    conn.executemany(
        "INSERT INTO clase VALUES (?, ?, '011')",
        [
            ("0111", "Cultivarea cerealelor"),
            ("0112", "Cultivarea orezului"),
        ],
    )
    conn.execute(
        "INSERT INTO api_keys (key_hash, name) VALUES (?, 'test-suite')",
        (hashlib.sha256(_VALID_API_KEY.encode()).hexdigest(),),
    )
    conn.executescript("""
        CREATE TABLE judete (
            cod_judet INTEGER PRIMARY KEY,
            denumire  TEXT NOT NULL
        );
        CREATE TABLE localitati (
            cod_siruta          INTEGER PRIMARY KEY,
            denumire            TEXT NOT NULL,
            denumire_diacritice TEXT,
            tip_cod             INTEGER NOT NULL,
            tip_abrev           TEXT NOT NULL,
            tip_denumire        TEXT NOT NULL,
            cod_judet           INTEGER NOT NULL
        );
        CREATE TABLE localitati_geo (
            gid           INTEGER PRIMARY KEY,
            nume_uat      TEXT NOT NULL,
            nume_uat_norm TEXT NOT NULL,
            natlevname    TEXT,
            natcode       TEXT,
            judet         TEXT NOT NULL,
            judet_norm    TEXT NOT NULL,
            lat           REAL,
            lon           REAL
        );
        CREATE TABLE cursuri_valutare (
            data          TEXT    NOT NULL,
            valuta        TEXT    NOT NULL,
            curs          REAL    NOT NULL,
            multiplicator INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (data, valuta)
        );
        CREATE TABLE zile_libere (
            data                          TEXT    NOT NULL,
            zi_saptamana                  TEXT    NOT NULL,
            denumire_sarbatoare           TEXT    NOT NULL,
            temei_art_139_codul_muncii    TEXT    NOT NULL,
            cade_in_weekend               INTEGER NOT NULL DEFAULT 0,
            observatii                    TEXT,
            sursa_legala                  TEXT    NOT NULL,
            sursa_calendar                TEXT,
            sursa_verificare_suplimentara TEXT,
            PRIMARY KEY (data, denumire_sarbatoare)
        );
    """)
    conn.executemany(
        "INSERT INTO judete VALUES (?, ?)",
        [
            (10, "BRASOV"),
            (41, "VRANCEA"),
        ],
    )
    conn.executemany(
        "INSERT INTO localitati (cod_siruta, denumire, denumire_diacritice, tip_cod, tip_abrev, tip_denumire, cod_judet) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (666, "FOCSANI", "FOCŞANI", 12, "Mun.", "Municipiu", 41),
            (667, "ADJUD",   "ADJUD",   13, "Or.",  "Oras",      41),
            (668, "PANCIU",  "PANCIU",  14, "Com.", "Comuna",    41),
            (100, "BRASOV",  "BRAŞOV",  12, "Mun.", "Municipiu", 10),
        ],
    )
    conn.executemany(
        "INSERT INTO localitati_geo (gid, nume_uat, nume_uat_norm, natlevname, natcode, judet, judet_norm, lat, lon) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "Focșani", "FOCSANI", "Municipiu", "SIRUTA-666", "Vrancea", "VRANCEA", 45.6967, 27.1858),
            (2, "Independența", "INDEPENDENTA", "Comuna", "SIRUTA-1001", "Constanța", "CONSTANTA", 44.2833, 27.7000),
            (3, "Independența", "INDEPENDENTA", "Comuna", "SIRUTA-1002", "Galați", "GALATI", 45.7333, 27.9333),
        ],
    )
    # EUR, USD (mult=1) and HUF (mult=100) across three trading days
    conn.executemany(
        "INSERT INTO cursuri_valutare VALUES (?, ?, ?, ?)",
        [
            ("2025-01-02", "EUR", 5.0000, 1),
            ("2025-01-03", "EUR", 5.0100, 1),
            ("2025-01-06", "EUR", 5.0200, 1),
            ("2025-01-02", "USD", 4.8000, 1),
            ("2025-01-03", "USD", 4.8100, 1),
            ("2025-01-06", "USD", 4.8200, 1),
            ("2025-01-02", "HUF", 1.2000, 100),
            ("2025-01-03", "HUF", 1.2100, 100),
        ],
    )
    conn.executemany(
        """
        INSERT INTO zile_libere (
            data,
            zi_saptamana,
            denumire_sarbatoare,
            temei_art_139_codul_muncii,
            cade_in_weekend,
            observatii,
            sursa_legala,
            sursa_calendar,
            sursa_verificare_suplimentara
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("2026-01-01", "joi", "Anul Nou - 1 ianuarie", "1 și 2 ianuarie", 0, None, "https://legislatie.just.ro/Public/DetaliiDocument/128647", "https://www.timeanddate.com/holidays/romania/2026", "https://zilelibere.com/"),
            ("2026-01-02", "vineri", "Anul Nou - 2 ianuarie", "1 și 2 ianuarie", 0, None, "https://legislatie.just.ro/Public/DetaliiDocument/128647", "https://www.timeanddate.com/holidays/romania/2026", "https://zilelibere.com/"),
            ("2026-01-06", "marți", "Botezul Domnului - Boboteaza", "6 ianuarie - Botezul Domnului - Boboteaza", 0, None, "https://legislatie.just.ro/Public/DetaliiDocument/128647", "https://www.timeanddate.com/holidays/romania/2026", "https://zilelibere.com/"),
            ("2026-01-07", "miercuri", "Soborul Sfântului Proroc Ioan Botezătorul", "7 ianuarie - Soborul Sfântului Proroc Ioan Botezătorul", 0, None, "https://legislatie.just.ro/Public/DetaliiDocument/128647", "https://www.timeanddate.com/holidays/romania/2026", "https://zilelibere.com/"),
            ("2026-06-01", "luni", "Ziua Copilului", "1 iunie", 0, "Aceeași dată cu a doua zi de Rusalii în 2026", "https://legislatie.just.ro/Public/DetaliiDocument/128647", "https://www.timeanddate.com/holidays/romania/2026", "https://zilelibere.com/"),
            ("2026-06-01", "luni", "Rusalii - a doua zi", "a doua zi de Rusalii", 0, "Aceeași dată cu Ziua Copilului în 2026", "https://legislatie.just.ro/Public/DetaliiDocument/128647", "https://www.timeanddate.com/holidays/romania/2026", "https://zilelibere.com/"),
            ("2026-12-26", "sâmbătă", "Crăciunul - a doua zi", "a doua zi de Crăciun", 1, None, "https://legislatie.just.ro/Public/DetaliiDocument/128647", "https://www.timeanddate.com/holidays/romania/2026", "https://zilelibere.com/"),
        ],
    )
    conn.commit()
    conn.close()


_seed_db(os.environ["SQLITE_DB"])


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture(scope="session")
def valid_api_key() -> str:
    return _VALID_API_KEY


@pytest.fixture(autouse=True)
def reset_rate_limits() -> None:
    """Reset in-memory rate limit counters before every test.

    Without this, tests accumulate quota against the shared TestClient IP and
    start failing with 429s partway through the suite.
    """
    limiter._storage.reset()
