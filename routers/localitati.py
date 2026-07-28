import unicodedata
import sqlite3

from fastapi import APIRouter, Depends, Path, Query, Request, HTTPException
from auth import limiter, _dynamic_limit, cached_json
from api_dependencies import get_sqlite_connection
from pydantic import BaseModel

router = APIRouter(
    prefix="/localitati",
    tags=["Localitati"],
)

# ---------------------------------------------------------------------------
# Schemas Localitati
# ---------------------------------------------------------------------------
class LocalitateGeoEntry(BaseModel):
    gid: int
    nume_uat: str
    natlevname: str | None
    natcode: str | None
    judet: str
    lat: float | None
    lon: float | None

class LocalitateGeoSearchResponse(BaseModel):
    total: int
    results: list[LocalitateGeoEntry]


# Future ideas kept here for later implementation:
# - /localitati/nearby?lat=&lon=&radius_km= — nearest-locality search via haversine distance;
# - /localitati/bbox?min_lat=&max_lat=&min_lon=&max_lon= — bounding-box query for map viewports;
# - /localitati/judete — distinct county name list from this table, useful if spellings
#   differ from /siruta/judete.


def _strip_diacritics(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )

def _norm_search(value: str) -> str:
    return _strip_diacritics(value.strip()).upper()

_LOCALITATI_GEO_COLUMNS = "gid, nume_uat, natlevname, natcode, judet, lat, lon"
_LOCALITATI_GEO_BASE_QUERY = f"SELECT {_LOCALITATI_GEO_COLUMNS} FROM localitati_geo"

# ---------------------------------------------------------------------------
# Endpoints Localitati
# ---------------------------------------------------------------------------

@router.get("/search", response_model=LocalitateGeoSearchResponse, summary="Cauta localitati dupa nume")
@limiter.limit(_dynamic_limit)
def search_localitati(
    request: Request,
    q: str = Query(..., min_length=2, description="Numele localitatii (ex: Focsani sau Focșani)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    pattern = f"%{_norm_search(q)}%"
    sql_where = " WHERE nume_uat_norm LIKE ? "

    total = conn.execute(
        f"SELECT COUNT(*) FROM localitati_geo {sql_where}",
        (pattern,),
    ).fetchone()[0]
    rows = conn.execute(
        _LOCALITATI_GEO_BASE_QUERY + sql_where + " ORDER BY nume_uat LIMIT ? OFFSET ?",
        (pattern, limit, offset),
    ).fetchall()

    return cached_json(request, {"total": total, "results": [dict(r) for r in rows]})

@router.get("/localitate/{nume}", response_model=list[LocalitateGeoEntry], summary="Cauta localitati dupa nume exact")
@limiter.limit(_dynamic_limit)
def get_localitati_by_nume(
    request: Request,
    nume: str = Path(..., description="Numele exact al localitatii (ex: Focșani)"),
    judet: str | None = Query(None, description="Filtrare dupa judet, pentru dezambiguizare (ex: Vrancea)"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    query = _LOCALITATI_GEO_BASE_QUERY + " WHERE nume_uat_norm = ?"
    params = [_norm_search(nume)]

    if judet is not None:
        query += " AND judet_norm = ?"
        params.append(_norm_search(judet))

    query += " ORDER BY judet ASC"

    rows = conn.execute(query, tuple(params)).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Localitatea '{nume}' nu a fost gasita.")
    return cached_json(request, [dict(r) for r in rows])

@router.get("/judet/{judet}", response_model=list[LocalitateGeoEntry], summary="Toate localitatile dintr-un judet")
@limiter.limit(_dynamic_limit)
def get_localitati_by_judet(
    request: Request,
    judet: str = Path(..., description="Numele judetului (ex: Cluj)"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    rows = conn.execute(
        _LOCALITATI_GEO_BASE_QUERY + " WHERE judet_norm = ? ORDER BY nume_uat ASC",
        (_norm_search(judet),),
    ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Judetul '{judet}' nu a fost gasit.")
    return cached_json(request, [dict(r) for r in rows])
