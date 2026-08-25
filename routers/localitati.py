import math
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


class LocalitateNearbyEntry(BaseModel):
    gid: int
    nume_uat: str
    judet: str
    lat: float
    lon: float
    distanta_km: float


class LocalitateNearbyResponse(BaseModel):
    lat: float
    lon: float
    radius_km: float
    total: int
    results: list[LocalitateNearbyEntry]


# Future ideas kept here for later implementation:
# - /localitati/bbox?min_lat=&max_lat=&min_lon=&max_lon= — bounding-box query for map viewports;
# - /localitati/judete — distinct county name list from this table, useful if spellings
#   differ from /siruta/judete.


_EARTH_RADIUS_KM = 6371.0088
_KM_PER_DEGREE_LAT = 111.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


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

@router.get(
    "/nearby",
    response_model=LocalitateNearbyResponse,
    summary="Localitati in raza unui punct",
    description=(
        "Localitati in raza `radius_km` fata de (`lat`, `lon`), ordonate crescator dupa "
        "distanta. Foloseste indexul spatial R-Tree `localitati_geo_rtree` pentru un "
        "pre-filtru rapid pe caseta englobanta, apoi calculeaza distanta exacta (haversine) "
        "doar pe candidatii din acea caseta. O casetă dreptunghiulară nu e un cerc exact, deci "
        "rafinarea cu distanta reala e obligatorie, nu doar o optimizare."
    ),
)
@limiter.limit(_dynamic_limit)
def get_localitati_nearby(
    request: Request,
    lat: float = Query(..., ge=-90, le=90, description="Latitudine"),
    lon: float = Query(..., ge=-180, le=180, description="Longitudine"),
    radius_km: float = Query(..., gt=0, le=500, description="Raza de cautare in kilometri"),
    limit: int = Query(50, ge=1, le=200),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    dlat = radius_km / _KM_PER_DEGREE_LAT
    # Aproximare deliberata: la lat=90 cos(lat)=0 face impartirea instabila, dar interogarea
    # foloseste tot caseta englobanta doar ca pre-filtru -- haversine de mai jos e cel care
    # decide efectiv ce intra in raza, deci o caseta putin prea larga la poli e inofensiva.
    cos_lat = max(math.cos(math.radians(lat)), 1e-6)
    dlon = radius_km / (111.320 * cos_lat)

    candidati = conn.execute(
        """
        SELECT gid FROM localitati_geo_rtree
        WHERE min_lat >= ? AND max_lat <= ? AND min_lon >= ? AND max_lon <= ?
        """,
        (lat - dlat, lat + dlat, lon - dlon, lon + dlon),
    ).fetchall()

    if not candidati:
        return cached_json(
            request, {"lat": lat, "lon": lon, "radius_km": radius_km, "total": 0, "results": []}
        )

    gids = [r["gid"] for r in candidati]
    placeholders = ",".join("?" for _ in gids)
    rows = conn.execute(
        f"SELECT gid, nume_uat, judet, lat, lon FROM localitati_geo WHERE gid IN ({placeholders})",
        gids,
    ).fetchall()

    results = []
    for r in rows:
        distanta = _haversine_km(lat, lon, r["lat"], r["lon"])
        if distanta <= radius_km:
            results.append(
                {
                    "gid": r["gid"],
                    "nume_uat": r["nume_uat"],
                    "judet": r["judet"],
                    "lat": r["lat"],
                    "lon": r["lon"],
                    "distanta_km": round(distanta, 3),
                }
            )
    results.sort(key=lambda item: item["distanta_km"])
    results = results[:limit]

    return cached_json(
        request,
        {"lat": lat, "lon": lon, "radius_km": radius_km, "total": len(results), "results": results},
    )
