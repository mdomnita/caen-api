import unicodedata

from fastapi import APIRouter, Path, Query, Request, HTTPException, Security
from auth import get_db, limiter, _dynamic_limit, cached_json, get_api_key
from pydantic import BaseModel

router = APIRouter(
    prefix="/siruta",
    tags=["Coduri SIRUTA"],
    dependencies=[Security(get_api_key)]
)

# ---------------------------------------------------------------------------
# Schemas SIRUTA
# ---------------------------------------------------------------------------
class Judet(BaseModel):
    cod_judet: int
    denumire: str

class LocalitateEntry(BaseModel):
    cod_siruta: int
    denumire: str
    tip_cod: int
    tip_abrev: str
    tip_denumire: str
    cod_judet: int
    judet_denumire: str

class LocalitateSearchResponse(BaseModel):
    total: int
    results: list[LocalitateEntry]


def _strip_diacritics(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )

_SIRUTA_BASE_QUERY = """
    SELECT l.cod_siruta, l.denumire, l.denumire_diacritice, l.tip_cod, l.tip_abrev, l.tip_denumire, l.cod_judet, j.denumire AS judet_denumire
    FROM localitati l
    JOIN judete j ON l.cod_judet = j.cod_judet
"""

# ---------------------------------------------------------------------------
# Endpoints SIRUTA
# ---------------------------------------------------------------------------

@router.get("/judete", response_model=list[Judet], summary="Toate judetele")
@limiter.limit(_dynamic_limit)
def list_judete(request: Request):
    with get_db() as conn:
        rows = conn.execute("SELECT cod_judet, denumire FROM judete ORDER BY denumire").fetchall()
    return cached_json(request, [dict(r) for r in rows])

@router.get("/localitate/{cod}", response_model=LocalitateEntry, summary="Cauta localitate dupa cod SIRUTA")
@limiter.limit(_dynamic_limit)
def get_localitate_by_siruta(
    request: Request, 
    cod: int = Path(..., description="Codul unic SIRUTA (numeric)")
):
    with get_db() as conn:
        row = conn.execute(_SIRUTA_BASE_QUERY + " WHERE l.cod_siruta = ?", (cod,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Codul SIRUTA {cod} nu a fost gasit.")
    return cached_json(request, dict(row))

@router.get("/cautare", response_model=LocalitateSearchResponse, summary="Cauta localitati dupa nume")
@limiter.limit(_dynamic_limit)
def search_localitati(
    request: Request,
    q: str = Query(..., min_length=2, description="Numele localitatii (ex: FOCSANI)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    # SQLite LIKE handles ASCII case-insensitively, but not Unicode case-folding for diacritics.
    # Search the ASCII column with a de-accented uppercase pattern and the diacritics column
    # with a lowercase Unicode-preserving pattern.
    query = q.strip()
    ascii_pattern = f"%{_strip_diacritics(query).upper()}%"
    diacritics_pattern = f"%{query.lower()}%"
    sql_where = " WHERE l.denumire LIKE ? OR l.denumire_diacritice LIKE ? "
    
    with get_db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM localitati l {sql_where}",
            (ascii_pattern, diacritics_pattern),
        ).fetchone()[0]
        rows = conn.execute(
            _SIRUTA_BASE_QUERY + sql_where + " ORDER BY l.denumire LIMIT ? OFFSET ?",
            (ascii_pattern, diacritics_pattern, limit, offset)
        ).fetchall()
        
    return cached_json(request, {"total": total, "results": [dict(r) for r in rows]})

@router.get("/judet/{cod_judet}", response_model=list[LocalitateEntry], summary="Toate localitatile dintr-un judet")
@limiter.limit(_dynamic_limit)
def get_localitati_by_judet(
    request: Request,
    cod_judet: int = Path(..., description="Codul judetului (ex: 41 pentru Vrancea)"),
    tip_cod: int | None = Query(None, description="Filtrare dupa tip ierarhie (ex: 12 pentru municipii)")
):
    query = _SIRUTA_BASE_QUERY + " WHERE l.cod_judet = ?"
    params = [cod_judet]
    
    if tip_cod is not None:
        query += " AND l.tip_cod = ?"
        params.append(tip_cod)
        
    query += " ORDER BY l.tip_cod ASC, l.denumire ASC"
    
    with get_db() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return cached_json(request, [dict(r) for r in rows])