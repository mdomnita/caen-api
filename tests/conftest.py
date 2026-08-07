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
        CREATE TABLE caen_v2 (
            cod      TEXT PRIMARY KEY,
            denumire TEXT NOT NULL
        );
        CREATE TABLE caen_corespondenta (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            cod_v2            TEXT,
            cod_v3            TEXT NOT NULL,
            tip_corespondenta TEXT NOT NULL
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
    conn.executemany(
        "INSERT INTO caen_v2 VALUES (?, ?)",
        [
            ("0111", "Cultivarea cerealelor v2"),
            ("0113", "Cultivarea legumelor v2"),
        ],
    )
    conn.executemany(
        "INSERT INTO caen_corespondenta (cod_v2, cod_v3, tip_corespondenta) VALUES (?, ?, ?)",
        [
            ("0111", "0111", "NESCHIMBAT"),
            ("0113", "0111", "MIXT"),
            ("0113", "0112", "DETALIERE"),
            (None,    "0112", "NOU"),
        ],
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
        CREATE TABLE coduri_postale (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            cod_postal               TEXT    NOT NULL,
            judet_raw                TEXT,
            judet_norm               TEXT    NOT NULL,
            cod_judet                INTEGER,
            localitate_raw           TEXT,
            localitate_norm          TEXT    NOT NULL,
            localitate_parinte_raw   TEXT,
            localitate_parinte_norm  TEXT,
            cod_siruta               INTEGER,
            siruta_sirsup            INTEGER,
            siruta_niv               INTEGER,
            sector                   INTEGER,
            tip_artera_raw           TEXT,
            tip_artera_norm          TEXT,
            strada_raw               TEXT,
            strada_norm              TEXT,
            numar_raw                TEXT,
            numar_tip                TEXT,
            numar_min                INTEGER,
            numar_max                INTEGER,
            numar_open_ended         INTEGER NOT NULL DEFAULT 0,
            numar_paritate           TEXT,
            oficiu_distribuire       TEXT,
            sursa                    TEXT    NOT NULL,
            sursa_versiune           TEXT    NOT NULL DEFAULT 'infocod-mai-2016'
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
    # coduri_postale: covers all 3 sursa values, a duplicate cod_postal (real
    # data has non-unique codes), open-ended/closed/bl-block numar variants,
    # a parsed parent-locality, and a row with NULL cod_siruta (real gap).
    conn.executemany(
        """
        INSERT INTO coduri_postale (
            cod_postal, judet_raw, judet_norm, cod_judet,
            localitate_raw, localitate_norm, localitate_parinte_raw, localitate_parinte_norm,
            cod_siruta, siruta_sirsup, siruta_niv, sector,
            tip_artera_raw, tip_artera_norm, strada_raw, strada_norm,
            numar_raw, numar_tip, numar_min, numar_max, numar_open_ended, numar_paritate,
            oficiu_distribuire, sursa
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            # Bucuresti: two streets sharing the same cod_postal (real data is non-unique)
            ("011357", "București", "BUCURESTI", 42, "București", "BUCURESTI", None, None,
             179141, 179132, 3, 1, "Stradă", "STRADA", "Mincu Ion, arh.", "MINCU ION, ARH.",
             "nr. 21-T", "nr", 21, None, 1, "impar", "București 2", "bucuresti"),
            ("011357", "București", "BUCURESTI", 42, "București", "BUCURESTI", None, None,
             179141, 179132, 3, 1, "Stradă", "STRADA", "Porumbaru Emanoil", "PORUMBARU EMANOIL",
             "nr. 1-25", "nr", 1, 25, 0, "impar", "București 2", "bucuresti"),
            # Orase (Focsani, judet 41): closed even range + a 'bl.' block row (no parsed range)
            ("620032", "Vrancea", "VRANCEA", 41, "Focșani", "FOCSANI", None, None,
             174753, 174744, 3, None, "Stradă", "STRADA", "Cuza Vodă", "CUZA VODA",
             "nr. 2-24", "nr", 2, 24, 0, "par", None, "oras"),
            ("620033", "Vrancea", "VRANCEA", 41, "Focșani", "FOCSANI", None, None,
             174753, 174744, 3, None, "Stradă", "STRADA", "Cuza Vodă", "CUZA VODA",
             "bl. T1, T2", "bl", None, None, 0, None, None, "oras"),
            # Orase (Focsani): numeric 'bl.' token whose digit (4) also falls
            # inside 620032's nr. 2-24 (par) range — the overlap case where a
            # numar filter can match both a street-number range and a block.
            ("620034", "Vrancea", "VRANCEA", 41, "Focșani", "FOCSANI", None, None,
             174753, 174744, 3, None, "Stradă", "STRADA", "Cuza Vodă", "CUZA VODA",
             "4", "bl", None, None, 0, None, None, "oras"),
            # Orase (Brasov, judet 10): different judet, open-ended odd range
            ("500001", "Brasov", "BRASOV", 10, "Brasov", "BRASOV", None, None,
             999999, 999998, 3, None, "Bulevard", "BULEVARD", "Eroilor", "EROILOR",
             "nr. 1-T", "nr", 1, None, 1, "impar", None, "oras"),
            # Sate (Vrancea): plain locality-level row
            ("625200", "Vrancea", "VRANCEA", 41, "Panciu", "PANCIU", None, None,
             668, None, None, None, None, None, None, None,
             None, None, None, None, 0, None, None, "sat"),
            # Sate: parsed parent-locality + NULL cod_siruta (real gap, 12 rows in source)
            ("625301", "Vrancea", "VRANCEA", 41, "Straoane", "STRAOANE", "Panciu", "PANCIU",
             None, None, None, None, None, None, None, None,
             None, None, None, None, 0, None, None, "sat"),
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
