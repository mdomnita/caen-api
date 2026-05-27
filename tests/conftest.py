"""
Pytest configuration and shared fixtures.

The temp DB is created and seeded at module load time — before pytest imports
any test file — so that DB_PATH is in the environment before main.py is first
imported (main.py reads DB_PATH at module level).
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
os.environ["DB_PATH"] = _tmp.name
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
    conn.commit()
    conn.close()


_seed_db(os.environ["DB_PATH"])


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
