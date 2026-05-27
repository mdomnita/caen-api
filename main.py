"""
API Romanian CAEN Codes – FastAPI + SQLite
"""
import hashlib
import json
import sqlite3
import os
from contextlib import contextmanager
from fastapi.responses import RedirectResponse
from fastapi.security import APIKeyHeader

from fastapi import FastAPI, HTTPException, Path, Query, Request, Response, Security
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

DB_PATH = os.getenv("DB_PATH", "caen.db")

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


_QUERY_BASE = """
    SELECT
        c.cod            AS cod_caen,
        c.denumire       AS denumire,
        s.cod            AS sectiune_cod,
        s.denumire       AS sectiune,
        d.cod            AS diviziune_cod,
        d.denumire       AS diviziune,
        g.cod            AS grupa_cod,
        g.denumire       AS grupa
    FROM   clase c
    JOIN   grupe      g ON c.grupa_cod     = g.cod
    JOIN   diviziuni  d ON g.diviziune_cod = d.cod
    JOIN   sectiuni   s ON d.sectiune_cod  = s.cod
"""


# ---------------------------------------------------------------------------
# API Key auth
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(name="X-API-KEY", auto_error=False)


def _is_valid_key(request: Request) -> bool:
    """Validate X-API-KEY against the api_keys table. Result cached on request.state."""
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
        return None  # anonymous – allowed, gets restrictive limit
    if not _is_valid_key(request):
        raise HTTPException(status_code=403, detail="Invalid API key.")
    return key


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def _rate_limit_key(request: Request) -> str:
    raw = request.headers.get("X-API-KEY")
    if raw and _is_valid_key(request):
        return f"auth:{hashlib.sha256(raw.encode()).hexdigest()}"  # each key gets its own bucket
    return get_remote_address(request)


def _dynamic_limit(key: str) -> str:
    return "1000/minute" if key.startswith("auth:") else "10/minute"


limiter = Limiter(key_func=_rate_limit_key)

app = FastAPI(
    title="Romanian CAEN Codes API",
    description=(
        "Cautare si navigare ierarhica a codurilor CAEN Rev. 3.\n\n"
        "**Ierarhie:** Sectiuni → Diviziuni → Grupe → Clase\n\n"
        "- `/sectiuni` — toate sectiunile\n"
        "- `/sectiuni/{cod}/diviziuni` — diviziunile unei sectiuni\n"
        "- `/diviziuni/{cod}/grupe` — grupele unei diviziuni\n"
        "- `/grupe/{cod}/clase` — clasele unei grupe (cu detalii complete)\n"
        "- `/caen/{cod}` — lookup direct dupa cod clasa (2-4 cifre)\n"
        "- `/caen?q=...` — cautare full-text in cod sau denumire"
    ),
    version="1.0.0",
    root_path="/api",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    dependencies=[Security(get_api_key)],  # validates key on every request; registers scheme in docs
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

ALLOWED_METHODS = {"GET"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=list(ALLOWED_METHODS),
    allow_headers=["*"],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "0"
        return response


app.add_middleware(SecurityHeadersMiddleware)


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

_CACHE_MAX_AGE = 86400  # 1 day — dataset is static


def _cached_json(request: Request, data: dict) -> Response:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    etag = f'"{hashlib.sha256(body).hexdigest()[:24]}"'
    headers = {"Cache-Control": f"public, max-age={_CACHE_MAX_AGE}", "ETag": etag}
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class Sectiune(BaseModel):
    cod: str
    denumire: str


class Diviziune(BaseModel):
    cod: str
    denumire: str
    sectiune_cod: str


class Grupa(BaseModel):
    cod: str
    denumire: str
    diviziune_cod: str


class CAENEntry(BaseModel):
    cod_caen: str
    denumire: str
    sectiune_cod: str
    sectiune: str
    diviziune_cod: str
    diviziune: str
    grupa_cod: str
    grupa: str


class SearchResponse(BaseModel):
    total: int
    results: list[CAENEntry]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
@limiter.limit(_dynamic_limit)
def root(request: Request):
    return RedirectResponse(url="/api/docs")


@app.get(
    "/caen/{cod}",
    response_model=CAENEntry,
    summary="Cauta dupa cod CAEN exact (4 cifre)",
)
@limiter.limit(_dynamic_limit)
def get_by_code(
    request: Request,
    cod: str = Path(pattern=r"^\d{2,4}$", description="Cod CAEN (2-4 cifre)"),
):
    """
    Returneaza detalii complete (denumire, sectiune, diviziune, grupa)
    pentru un cod CAEN de 2-4 cifre.
    """
    with get_db() as conn:
        row = conn.execute(
            _QUERY_BASE + " WHERE c.cod = ?", (cod.strip(),)
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Codul CAEN '{cod}' nu a fost gasit.")

    return _cached_json(request, dict(row))


@app.get(
    "/caen",
    response_model=SearchResponse,
    summary="Cauta coduri CAEN dupa cod sau denumire",
)
@limiter.limit(_dynamic_limit)
def search(
    request: Request,
    q: str = Query(..., min_length=1, description="Text de cautare (cod sau parte din denumire)"),
    limit: int = Query(50, ge=1, le=200, description="Numar maxim de rezultate"),
    offset: int = Query(0, ge=0, description="Paginare – pozitia de start"),
):
    """
    Cauta coduri CAEN dupa cod partial sau text din denumire.
    Exemplu: `/caen?q=0111` sau `/caen?q=cereale`
    """
    pattern = f"%{q.strip()}%"
    sql_where = " WHERE c.cod LIKE ? OR c.denumire LIKE ? "

    with get_db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM clase c {sql_where}", (pattern, pattern)
        ).fetchone()[0]

        rows = conn.execute(
            _QUERY_BASE + sql_where + " ORDER BY c.cod LIMIT ? OFFSET ?",
            (pattern, pattern, limit, offset),
        ).fetchall()

    return _cached_json(request, {"total": total, "results": [dict(r) for r in rows]})


@app.get(
    "/sectiuni",
    response_model=list[Sectiune],
    summary="Listeaza toate sectiunile CAEN",
    tags=["Ierarhie"],
)
@limiter.limit(_dynamic_limit)
def list_sectiuni(request: Request):
    """Returneaza lista tuturor sectiunilor CAEN Rev. 3, ordonate dupa cod."""
    with get_db() as conn:
        rows = conn.execute("SELECT cod, denumire FROM sectiuni ORDER BY cod").fetchall()
    return _cached_json(request, [dict(r) for r in rows])


@app.get(
    "/sectiuni/{cod}",
    response_model=Sectiune,
    summary="Detalii sectiune dupa cod",
    tags=["Ierarhie"],
)
@limiter.limit(_dynamic_limit)
def get_sectiune(
    request: Request,
    cod: str = Path(pattern=r"^[A-Za-z]{1,2}$", description="Cod sectiune (litera, ex: A)"),
):
    """Returneaza denumirea sectiunii identificate prin codul dat."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT cod, denumire FROM sectiuni WHERE cod = ?", (cod.upper(),)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Sectiunea '{cod}' nu a fost gasita.")
    return _cached_json(request, dict(row))


@app.get(
    "/sectiuni/{cod}/diviziuni",
    response_model=list[Diviziune],
    summary="Diviziunile unei sectiuni",
    tags=["Ierarhie"],
)
@limiter.limit(_dynamic_limit)
def list_diviziuni_by_sectiune(
    request: Request,
    cod: str = Path(pattern=r"^[A-Za-z]{1,2}$", description="Cod sectiune (litera, ex: A)"),
):
    """Returneaza toate diviziunile din sectiunea specificata."""
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM sectiuni WHERE cod = ?", (cod.upper(),)).fetchone():
            raise HTTPException(status_code=404, detail=f"Sectiunea '{cod}' nu a fost gasita.")
        rows = conn.execute(
            "SELECT cod, denumire, sectiune_cod FROM diviziuni WHERE sectiune_cod = ? ORDER BY cod",
            (cod.upper(),),
        ).fetchall()
    return _cached_json(request, [dict(r) for r in rows])


@app.get(
    "/diviziuni/{cod}",
    response_model=Diviziune,
    summary="Detalii diviziune dupa cod",
    tags=["Ierarhie"],
)
@limiter.limit(_dynamic_limit)
def get_diviziune(
    request: Request,
    cod: str = Path(pattern=r"^\d{2}$", description="Cod diviziune (2 cifre, ex: 01)"),
):
    """Returneaza denumirea si sectiunea parinte a diviziunii."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT cod, denumire, sectiune_cod FROM diviziuni WHERE cod = ?", (cod,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Diviziunea '{cod}' nu a fost gasita.")
    return _cached_json(request, dict(row))


@app.get(
    "/diviziuni/{cod}/grupe",
    response_model=list[Grupa],
    summary="Grupele unei diviziuni",
    tags=["Ierarhie"],
)
@limiter.limit(_dynamic_limit)
def list_grupe_by_diviziune(
    request: Request,
    cod: str = Path(pattern=r"^\d{2}$", description="Cod diviziune (2 cifre, ex: 01)"),
):
    """Returneaza toate grupele din diviziunea specificata."""
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM diviziuni WHERE cod = ?", (cod,)).fetchone():
            raise HTTPException(status_code=404, detail=f"Diviziunea '{cod}' nu a fost gasita.")
        rows = conn.execute(
            "SELECT cod, denumire, diviziune_cod FROM grupe WHERE diviziune_cod = ? ORDER BY cod",
            (cod,),
        ).fetchall()
    return _cached_json(request, [dict(r) for r in rows])


@app.get(
    "/grupe/{cod}",
    response_model=Grupa,
    summary="Detalii grupa dupa cod",
    tags=["Ierarhie"],
)
@limiter.limit(_dynamic_limit)
def get_grupa(
    request: Request,
    cod: str = Path(pattern=r"^\d{3}$", description="Cod grupa (3 cifre, ex: 011)"),
):
    """Returneaza denumirea si diviziunea parinte a grupei."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT cod, denumire, diviziune_cod FROM grupe WHERE cod = ?", (cod,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Grupa '{cod}' nu a fost gasita.")
    return _cached_json(request, dict(row))


@app.get(
    "/grupe/{cod}/clase",
    response_model=list[CAENEntry],
    summary="Clasele (coduri CAEN) dintr-o grupa",
    tags=["Ierarhie"],
)
@limiter.limit(_dynamic_limit)
def list_clase_by_grupa(
    request: Request,
    cod: str = Path(pattern=r"^\d{3}$", description="Cod grupa (3 cifre, ex: 011)"),
):
    """Returneaza toate clasele CAEN din grupa specificata, cu detalii complete."""
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM grupe WHERE cod = ?", (cod,)).fetchone():
            raise HTTPException(status_code=404, detail=f"Grupa '{cod}' nu a fost gasita.")
        rows = conn.execute(
            _QUERY_BASE + " WHERE g.cod = ? ORDER BY c.cod", (cod,)
        ).fetchall()
    return _cached_json(request, [dict(r) for r in rows])


@app.get("/health", include_in_schema=False)
@limiter.limit(_dynamic_limit)
def health(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok"}
