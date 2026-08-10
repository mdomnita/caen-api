import sqlite3

import requests
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel

from auth import limiter, _dynamic_limit, cached_json
from api_dependencies import get_sqlite_connection
from helpers.text_normalization import normalize_search
from services.geocoding import GeocodingProvider, get_geocoding_provider

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


class RezolvareCandidate(BaseModel):
    source: str  # "local" or "provider:<name>"
    judet: str | None
    localitate: str | None
    strada: str | None
    cod_postal: str | None
    cod_siruta: int | None
    lat: float | None
    lon: float | None
    score: float | None
    formatted_address: str | None


class RezolvareResponse(BaseModel):
    query: str
    candidates: list[RezolvareCandidate]


# Future ideas kept here for later implementation:
# - fuzzy/typo-tolerant search if substring matching proves too strict.

_AUTOCOMPLETE_COLUMNS = {
    "judet": "judet_raw",
    "localitate": "localitate_raw",
    "strada": "strada_raw",
}

_REZOLVARE_MAX_CANDIDATES = 5


def _serialize_row(row: sqlite3.Row) -> dict:
    payload = dict(row)
    payload["numar_open_ended"] = bool(payload["numar_open_ended"])
    return payload


def _find_local_candidates(conn: sqlite3.Connection, adresa: str, limit: int) -> list[dict]:
    """Best-effort local match: does the (normalized) free-form address text
    contain a known locality name, and within it a known street name?

    This is a simple containment heuristic, not a real address parser — it
    intentionally returns *multiple* candidates rather than guessing when
    several localities/judete share a name (see plan: never silently pick one
    guess for ambiguous input). Scans coduri_postale's distinct localities
    per request; fine at this table's size (~55k rows), not indexed for it.
    """
    query_norm = normalize_search(adresa)

    localitate_rows = conn.execute(
        """
        SELECT DISTINCT localitate_raw, localitate_norm, judet_raw, judet_norm
        FROM coduri_postale
        WHERE length(localitate_norm) >= 3 AND instr(?, localitate_norm) > 0
        """,
        (query_norm,),
    ).fetchall()

    # Many locality names repeat across several judete (e.g. several dozen
    # villages named "Cuza Vodă" exist across different counties). If the
    # query text also names the județ, that's a far stronger signal than
    # name length alone — rank those matches first so a genuinely specific
    # address ("...Focșani, Vrancea") doesn't get crowded out of the top-N
    # by same-named localities the query never mentions.
    localitate_rows = sorted(
        localitate_rows,
        key=lambda r: (r["judet_norm"] in query_norm, len(r["localitate_norm"])),
        reverse=True,
    )

    candidates: list[dict] = []
    for loc_row in localitate_rows:
        if len(candidates) >= limit:
            break

        strada_rows = conn.execute(
            """
            SELECT DISTINCT strada_raw, strada_norm
            FROM coduri_postale
            WHERE localitate_norm = ? AND strada_norm IS NOT NULL
              AND length(strada_norm) >= 3 AND instr(?, strada_norm) > 0
            ORDER BY length(strada_norm) DESC
            LIMIT 1
            """,
            (loc_row["localitate_norm"], query_norm),
        ).fetchall()

        cp_params: list[str] = [loc_row["judet_norm"], loc_row["localitate_norm"]]
        strada_condition = ""
        if strada_rows:
            strada_condition = " AND strada_norm = ?"
            cp_params.append(strada_rows[0]["strada_norm"])

        cp_rows = conn.execute(
            f"""
            SELECT DISTINCT cod_postal, cod_siruta FROM coduri_postale
            WHERE judet_norm = ? AND localitate_norm = ?{strada_condition}
            ORDER BY cod_postal
            LIMIT 3
            """,
            cp_params,
        ).fetchall()

        strada_raw = strada_rows[0]["strada_raw"] if strada_rows else None
        for cp_row in cp_rows:
            candidates.append({
                "source": "local",
                "judet": loc_row["judet_raw"],
                "localitate": loc_row["localitate_raw"],
                "strada": strada_raw,
                "cod_postal": cp_row["cod_postal"],
                "cod_siruta": cp_row["cod_siruta"],
                "lat": None,
                "lon": None,
                "score": None,
                "formatted_address": None,
            })
            if len(candidates) >= limit:
                break

    return candidates

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
    numar_tip: str | None = Query(
        None, pattern="^(nr|bl)$",
        description=(
            "Filtreaza dupa tipul intrarii: 'nr' (numar de strada) sau 'bl' (numar de bloc). "
            "Fara acest parametru, un filtru 'numar' poate potrivi ambele tipuri — o strada poate "
            "avea atat un interval de numere cat si un bloc cu acelasi numar/eticheta."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    if not any([judet, localitate, strada, numar, numar_tip]):
        raise HTTPException(
            status_code=400,
            detail="Specificati cel putin un filtru: judet, localitate, strada, numar sau numar_tip.",
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
        numar_paritate = "par" if numar % 2 == 0 else "impar"
        conditions.append(
            "("
            "(numar_min IS NOT NULL AND numar_max IS NOT NULL AND numar_min <= ? AND ? <= numar_max)"
            " OR "
            "(numar_open_ended = 1 AND numar_min IS NOT NULL AND ? >= numar_min)"
            " OR "
            "(numar_open_ended = 0 AND numar_raw = ?)"
            ")"
            " AND (numar_paritate IS NULL OR numar_paritate = ?)"
        )
        params.extend([numar, numar, numar, numar, numar_paritate])
    if numar_tip:
        conditions.append("numar_tip = ?")
        params.append(numar_tip)

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


@router.get(
    "/rezolvare",
    response_model=RezolvareResponse,
    summary="Rezolva o adresa in format liber",
    description=(
        "Incearca mai intai o potrivire locala (judet/localitate/strada, cautate ca substring "
        "in textul adresei) folosind exclusiv datele proprii, gratuite. Daca nu gaseste nicio "
        "potrivire locala, cade pe furnizorul de geocodare (ArcGIS, gratuit/anonim) pentru o "
        "aproximare lat/lon. Rezultatele furnizorului extern nu sunt niciodata stocate in baza "
        "de date, doar servite din cache HTTP standard. Returneaza pana la 5 candidati "
        "clasificati prin `source`, fara sa aleaga silentios unul singur pentru input ambiguu."
    ),
)
@limiter.limit(_dynamic_limit)
def rezolva_adresa(
    request: Request,
    adresa: str = Query(..., min_length=5, description="Adresa in format liber, ex: 'Str. Cuza Voda 10, Focsani, Vrancea'"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
    provider: GeocodingProvider = Depends(get_geocoding_provider),
):
    candidates = _find_local_candidates(conn, adresa, _REZOLVARE_MAX_CANDIDATES)

    if not candidates:
        try:
            result = provider.geocode(adresa)
        except requests.RequestException:
            result = None

        if result is not None:
            candidates = [{
                "source": f"provider:{result.provider}",
                "judet": None,
                "localitate": None,
                "strada": None,
                "cod_postal": None,
                "cod_siruta": None,
                "lat": result.lat,
                "lon": result.lon,
                "score": result.score,
                "formatted_address": result.formatted_address,
            }]

    return cached_json(request, {"query": adresa, "candidates": candidates[:_REZOLVARE_MAX_CANDIDATES]})


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
