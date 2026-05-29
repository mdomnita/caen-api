import os
import sqlite3
import hashlib
from contextlib import contextmanager
from fastapi import Request, HTTPException, Security, Response
from fastapi.security import APIKeyHeader
from slowapi import Limiter
from slowapi.util import get_remote_address

DB_PATH = os.getenv("DB_PATH", "caen.db")
_CACHE_MAX_AGE = 86400

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

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
    return "1000/minute" if key.startswith("auth:") else "10/minute"

limiter = Limiter(key_func=_rate_limit_key)

def cached_json(request: Request, data: dict | list) -> Response:
    import json
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    etag = f'"{hashlib.sha256(body).hexdigest()[:24]}"'
    headers = {"Cache-Control": f"public, max-age={_CACHE_MAX_AGE}", "ETag": etag}
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)