import sqlite3

from fastapi import APIRouter, Depends, Path, Query, Request, HTTPException
from pydantic import BaseModel
from auth import limiter, _dynamic_limit, cached_json
from api_dependencies import get_sqlite_connection

router = APIRouter(prefix="/caen", tags=["Coduri CAEN"])

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


# Future ideas kept here for later implementation:
# - add reverse lookup endpoints by sectiune/diviziune/grupa keywords, not only by class text;
# - expose related CAEN codes based on the same grupa or diviziune for discovery flows;
# - add an autocomplete endpoint optimized for short prefixes and UI search boxes;
# - add a diff/alias endpoint if future CAEN revisions or historical mappings are imported.


@router.get(
    "/{cod}",
    response_model=CAENEntry,
    summary="Cauta dupa cod CAEN exact (4 cifre)",
)
@limiter.limit(_dynamic_limit)
def get_by_code(
    request: Request,
    cod: str = Path(pattern=r"^\d{2,4}$", description="Cod CAEN (2-4 cifre)"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    """
    Returneaza detalii complete (denumire, sectiune, diviziune, grupa)
    pentru un cod CAEN de 2-4 cifre.
    """
    row = conn.execute(
        _QUERY_BASE + " WHERE c.cod = ?", (cod.strip(),)
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Codul CAEN '{cod}' nu a fost gasit.")

    return cached_json(request, dict(row))


@router.get(
    "",
    response_model=SearchResponse,
    summary="Cauta coduri CAEN dupa cod sau denumire",
)
@limiter.limit(_dynamic_limit)
def search(
    request: Request,
    q: str = Query(..., min_length=1, description="Text de cautare (cod sau parte din denumire)"),
    limit: int = Query(50, ge=1, le=200, description="Numar maxim de rezultate"),
    offset: int = Query(0, ge=0, description="Paginare – pozitia de start"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    """
    Cauta coduri CAEN dupa cod partial sau text din denumire.
    Exemplu: `/caen?q=0111` sau `/caen?q=cereale`
    """
    pattern = f"%{q.strip()}%"
    sql_where = " WHERE c.cod LIKE ? OR c.denumire LIKE ? "

    total = conn.execute(
        f"SELECT COUNT(*) FROM clase c {sql_where}", (pattern, pattern)
    ).fetchone()[0]

    rows = conn.execute(
        _QUERY_BASE + sql_where + " ORDER BY c.cod LIMIT ? OFFSET ?",
        (pattern, pattern, limit, offset),
    ).fetchall()

    return cached_json(request, {"total": total, "results": [dict(r) for r in rows]})
