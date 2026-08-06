import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel

from auth import limiter, _dynamic_limit, cached_json
from api_dependencies import get_sqlite_connection
from helpers.text_normalization import normalize_search

router = APIRouter(
    prefix="/coduripostale",
    tags=["Coduri postale"],
)

# ---------------------------------------------------------------------------
# Schemas Coduri postale
# ---------------------------------------------------------------------------
class CodPostalEntry(BaseModel):
    id: int
    cod_postal: str
    judet_raw: str | None
    judet_norm: str
    cod_judet: int | None
    localitate_raw: str | None
    localitate_norm: str
    localitate_parinte_raw: str | None
    localitate_parinte_norm: str | None
    cod_siruta: int | None
    siruta_sirsup: int | None
    siruta_niv: int | None
    sector: int | None
    tip_artera_raw: str | None
    tip_artera_norm: str | None
    strada_raw: str | None
    strada_norm: str | None
    numar_raw: str | None
    numar_tip: str | None
    numar_min: int | None
    numar_max: int | None
    numar_open_ended: bool
    numar_paritate: str | None
    oficiu_distribuire: str | None
    sursa: str
    sursa_versiune: str


class CodPostalSearchResponse(BaseModel):
    total: int
    results: list[CodPostalEntry]


class AutocompleteResponse(BaseModel):
    results: list[str]


# Future ideas kept here for later implementation:
# - /coduripostale/rezolvare — free-form address resolution with ArcGIS fallback;
# - fuzzy/typo-tolerant search if substring matching proves too strict.

_AUTOCOMPLETE_COLUMNS = {
    "judet": "judet_raw",
    "localitate": "localitate_raw",
    "strada": "strada_raw",
}


def _serialize_row(row: sqlite3.Row) -> dict:
    payload = dict(row)
    payload["numar_open_ended"] = bool(payload["numar_open_ended"])
    return payload

# ---------------------------------------------------------------------------
# Endpoints Coduri postale
# ---------------------------------------------------------------------------

@router.get("/cautare", response_model=CodPostalSearchResponse, summary="Cauta coduri postale dupa judet/localitate/strada/numar")
@limiter.limit(_dynamic_limit)
def search_coduri_postale(
    request: Request,
    judet: str | None = Query(None, description="Numele judetului (ex: Vrancea)"),
    localitate: str | None = Query(None, description="Numele localitatii (ex: Focsani)"),
    strada: str | None = Query(None, min_length=2, description="Text partial din denumirea strazii"),
    numar: int | None = Query(None, ge=1, description="Numarul strazii; potriveste intervalele parsate din sursa"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    if not any([judet, localitate, strada, numar]):
        raise HTTPException(
            status_code=400,
            detail="Specificati cel putin un filtru: judet, localitate, strada sau numar.",
        )

    conditions: list[str] = []
    params: list[str | int] = []

    if judet:
        conditions.append("judet_norm = ?")
        params.append(normalize_search(judet))
    if localitate:
        conditions.append("localitate_norm = ?")
        params.append(normalize_search(localitate))
    if strada:
        conditions.append("strada_norm LIKE ?")
        params.append(f"%{normalize_search(strada)}%")
    if numar is not None:
        conditions.append(
            "("
            "(numar_min IS NOT NULL AND numar_max IS NOT NULL AND numar_min <= ? AND ? <= numar_max)"
            " OR "
            "(numar_open_ended = 1 AND numar_min IS NOT NULL AND ? >= numar_min)"
            ")"
        )
        params.extend([numar, numar, numar])

    where_clause = " WHERE " + " AND ".join(conditions)

    total = conn.execute(
        f"SELECT COUNT(*) FROM coduri_postale{where_clause}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM coduri_postale{where_clause} ORDER BY cod_postal LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()

    return cached_json(request, {"total": total, "results": [_serialize_row(r) for r in rows]})


@router.get("/autocomplete", response_model=AutocompleteResponse, summary="Sugestii judet/localitate/strada (type-ahead)")
@limiter.limit(_dynamic_limit)
def autocomplete_coduri_postale(
    request: Request,
    tip: str = Query(..., pattern="^(judet|localitate|strada)$", description="Ce se completeaza: judet, localitate sau strada"),
    q: str = Query(..., min_length=2, description="Prefix de cautare"),
    localitate: str | None = Query(None, description="Necesar pentru tip=strada, pentru a restrange rezultatele la o localitate"),
    limit: int = Query(10, ge=1, le=20),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    if tip == "strada" and not localitate:
        raise HTTPException(
            status_code=400,
            detail="Parametrul 'localitate' este obligatoriu pentru tip=strada.",
        )

    column_raw = _AUTOCOMPLETE_COLUMNS[tip]
    column_norm = column_raw.replace("_raw", "_norm")
    pattern = f"{normalize_search(q)}%"

    where_clause = f" WHERE {column_norm} LIKE ?"
    params: list[str] = [pattern]

    if tip == "strada":
        where_clause += " AND localitate_norm = ?"
        params.append(normalize_search(localitate))

    where_clause += f" AND {column_raw} IS NOT NULL"

    rows = conn.execute(
        f"SELECT DISTINCT {column_raw} FROM coduri_postale{where_clause} ORDER BY {column_raw} LIMIT ?",
        [*params, limit],
    ).fetchall()

    return cached_json(request, {"results": [row[0] for row in rows]})


@router.get("/{cod}", response_model=list[CodPostalEntry], summary="Cauta dupa codul postal")
@limiter.limit(_dynamic_limit)
def get_by_cod_postal(
    request: Request,
    cod: str = Path(..., pattern=r"^\d{6}$", description="Codul postal (6 cifre, ex: 011357)"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    rows = conn.execute(
        "SELECT * FROM coduri_postale WHERE cod_postal = ? ORDER BY id", (cod,)
    ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Codul postal {cod} nu a fost gasit.")
    return cached_json(request, [_serialize_row(r) for r in rows])
