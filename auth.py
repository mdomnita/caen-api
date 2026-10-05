import os
import sqlite3
import hashlib
from datetime import datetime, timezone
from contextlib import contextmanager
from fastapi import Request, HTTPException, Security, Response
from fastapi.security import APIKeyHeader
from slowapi import Limiter
from slowapi.util import get_remote_address

SQLITE_DB = os.getenv("SQLITE_DB", "caen.db")
_CACHE_MAX_AGE = 86400
_REQUEST_LOG_MAX_ROWS = max(1, int(os.getenv("REQUEST_LOG_MAX_ROWS", "50000")))

@contextmanager
def get_db():
    # check_same_thread=False: FastAPI's sync-dependency wrapper opens and
    # closes this connection via two separate threadpool calls that aren't
    # guaranteed to land on the same OS thread. That's fine here (each
    # connection is only ever used by one request at a time, never
    # concurrently) — we just need sqlite3 to not enforce same-thread reuse.
    conn = sqlite3.connect(SQLITE_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
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
            CREATE TABLE IF NOT EXISTS api_ip_request_counts (
                client_ip     TEXT PRIMARY KEY,
                request_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS api_route_request_counts (
                route_template TEXT PRIMARY KEY,
                request_count  INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS api_recent_requests (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                logged_at      TEXT NOT NULL,
                method         TEXT NOT NULL,
                route_template TEXT NOT NULL,
                status_code    INTEGER NOT NULL
            );
        """)
        conn.commit()


def log_api_request(
    request: Request,
    status_code: int,
) -> None:
    logged_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    client_ip = request.client.host if request.client else "unknown"
    matched_route = request.scope.get("route")
    # Starlette exposes the declared route after call_next() returns. Using
    # that template prevents values such as a CUI from creating one counter
    # per requested resource. Unknown paths share one bounded bucket too.
    route_template = getattr(matched_route, "path", None) or "__unmatched__"

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO api_ip_request_counts (client_ip, request_count)
            VALUES (?, 1)
            ON CONFLICT(client_ip)
            DO UPDATE SET request_count = request_count + 1
            """,
            (client_ip,),
        )
        conn.execute(
            """
            INSERT INTO api_route_request_counts (route_template, request_count)
            VALUES (?, 1)
            ON CONFLICT(route_template)
            DO UPDATE SET request_count = request_count + 1
            """,
            (route_template,),
        )
        conn.execute(
            """
            INSERT INTO api_recent_requests (
                logged_at, method, route_template, status_code
            ) VALUES (?, ?, ?, ?)
            """,
            (logged_at, request.method, route_template, status_code),
        )
        conn.execute(
            """
            DELETE FROM api_recent_requests
            WHERE id <= last_insert_rowid() - ?
            """,
            (_REQUEST_LOG_MAX_ROWS,),
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
