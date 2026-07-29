import os
import sqlite3
import hashlib
from datetime import date, datetime, timezone
from contextlib import contextmanager
from fastapi import Request, HTTPException, Security, Response
from fastapi.security import APIKeyHeader
from slowapi import Limiter
from slowapi.util import get_remote_address

SQLITE_DB = os.getenv("SQLITE_DB", "caen.db")
_CACHE_MAX_AGE = 86400

@contextmanager
def get_db():
    # check_same_thread=False: FastAPI's sync-dependency wrapper opens and
    # closes this connection via two separate threadpool calls that aren't
    # guaranteed to land on the same OS thread. That's fine here (each
    # connection is only ever used by one request at a time, never
    # concurrently) — we just need sqlite3 to not enforce same-thread reuse.
    conn = sqlite3.connect(SQLITE_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # synchronous is a per-connection setting (unlike journal_mode, which is
    # persisted in the file); NORMAL is the pairing SQLite recommends for WAL
    # mode, and avoids an fsync-equivalent flush on every commit.
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
    finally:
        conn.close()


def ensure_observability_tables() -> None:
    with get_db() as conn:
        # journal_mode is stored in the database file itself, so this only
        # needs to run once (here, at startup) rather than per connection.
        # WAL lets readers and the request-logging writer proceed
        # concurrently instead of serializing on a single writer lock.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_request_logs (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                logged_at        TEXT NOT NULL,
                logged_date      TEXT NOT NULL,
                method           TEXT NOT NULL,
                path             TEXT NOT NULL,
                query_string     TEXT,
                status_code      INTEGER NOT NULL,
                duration_ms      REAL NOT NULL,
                client_ip        TEXT,
                is_authenticated INTEGER NOT NULL DEFAULT 0,
                api_key_hash     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_api_request_logs_logged_date
                ON api_request_logs(logged_date);
            CREATE INDEX IF NOT EXISTS idx_api_request_logs_path_date
                ON api_request_logs(path, logged_date);

            CREATE TABLE IF NOT EXISTS api_daily_stats (
                logged_date          TEXT NOT NULL,
                method               TEXT NOT NULL,
                path                 TEXT NOT NULL,
                status_code          INTEGER NOT NULL,
                request_count        INTEGER NOT NULL DEFAULT 0,
                authenticated_count  INTEGER NOT NULL DEFAULT 0,
                total_duration_ms    REAL NOT NULL DEFAULT 0,
                min_duration_ms      REAL NOT NULL,
                max_duration_ms      REAL NOT NULL,
                last_logged_at       TEXT NOT NULL,
                PRIMARY KEY (logged_date, method, path, status_code)
            );
        """)
        conn.commit()


def log_api_request(
    request: Request,
    status_code: int,
    duration_ms: float,
) -> None:
    logged_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    logged_date = date.today().isoformat()
    raw_api_key = request.headers.get("X-API-KEY")
    api_key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest() if raw_api_key else None
    is_authenticated = int(bool(raw_api_key) and getattr(request.state, "api_key_valid", False))
    client = request.client.host if request.client else None
    path = request.url.path
    query_string = request.url.query or None

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO api_request_logs (
                logged_at,
                logged_date,
                method,
                path,
                query_string,
                status_code,
                duration_ms,
                client_ip,
                is_authenticated,
                api_key_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                logged_at,
                logged_date,
                request.method,
                path,
                query_string,
                status_code,
                duration_ms,
                client,
                is_authenticated,
                api_key_hash,
            ),
        )
        conn.execute(
            """
            INSERT INTO api_daily_stats (
                logged_date,
                method,
                path,
                status_code,
                request_count,
                authenticated_count,
                total_duration_ms,
                min_duration_ms,
                max_duration_ms,
                last_logged_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            ON CONFLICT(logged_date, method, path, status_code)
            DO UPDATE SET
                request_count = request_count + 1,
                authenticated_count = authenticated_count + excluded.authenticated_count,
                total_duration_ms = total_duration_ms + excluded.total_duration_ms,
                min_duration_ms = MIN(min_duration_ms, excluded.min_duration_ms),
                max_duration_ms = MAX(max_duration_ms, excluded.max_duration_ms),
                last_logged_at = excluded.last_logged_at
            """,
            (
                logged_date,
                request.method,
                path,
                status_code,
                is_authenticated,
                duration_ms,
                duration_ms,
                duration_ms,
                logged_at,
            ),
        )
        conn.commit()

_api_key_header = APIKeyHeader(name="X-API-KEY", auto_error=False)

def _is_valid_key(request: Request) -> bool:
    if hasattr(request.state, "api_key_valid"):
        return request.state.api_key_valid
    raw = request.headers.get("X-API-KEY")
    if not raw:
        request.state.api_key_valid = False
        return False
    h = hashlib.sha256(raw.encode()).hexdigest()
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM api_keys WHERE key_hash = ? AND is_active = 1", (h,)
        ).fetchone()
    request.state.api_key_valid = row is not None
    return request.state.api_key_valid

def get_api_key(request: Request, key: str | None = Security(_api_key_header)) -> str | None:
    if key is None:
        return None
    if not _is_valid_key(request):
        raise HTTPException(status_code=403, detail="Invalid API key.")
    return key

def _rate_limit_key(request: Request) -> str:
    raw = request.headers.get("X-API-KEY")
    if raw and _is_valid_key(request):
        return f"auth:{hashlib.sha256(raw.encode()).hexdigest()}"
    return get_remote_address(request)

def _dynamic_limit(key: str) -> str:
    return "100/minute" if key.startswith("auth:") else "10/minute"

limiter = Limiter(key_func=_rate_limit_key)

def cached_json(request: Request, data: dict | list) -> Response:
    import json
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    etag = f'"{hashlib.sha256(body).hexdigest()[:24]}"'
    headers = {"Cache-Control": f"public, max-age={_CACHE_MAX_AGE}", "ETag": etag}
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)