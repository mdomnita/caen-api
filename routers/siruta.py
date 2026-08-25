import unicodedata
import sqlite3

from fastapi import APIRouter, Depends, Path, Query, Request, HTTPException
from auth import limiter, _dynamic_limit, cached_json
from api_dependencies import get_sqlite_connection
from pydantic import BaseModel

router = APIRouter(
    prefix="/siruta",
    tags=["Coduri SIRUTA"],
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


# NOU: judet extins (regiune, abreviere auto, cod SIRUTA propriu), regiuni de dezvoltare,
# si localitati componente (sate) -- vezi init_siruta_extins() in scripts/init_siruta_db.py.
class JudetDetail(BaseModel):
    cod_judet: int
    denumire: str
    abbr: str | None
    cod_regiune: int | None
    regiune_denumire: str | None
    cod_siruta_judet: int | None

class RegiuneEntry(BaseModel):
    cod_regiune: int
    denumire: str
    nuts2: str

class ComponentaEntry(BaseModel):
    cod_siruta: int
    denumire: str
    tip_cod: int
    tip_denumire: str
    cod_siruta_parinte: int
    cod_judet: int
    judet_denumire: str
    lat: float | None
    lon: float | None
    coduri_postale: list[str]

class ComponenteResponse(BaseModel):
    parinte: LocalitateEntry
    total: int
    results: list[ComponentaEntry]

class TipLocalitateEntry(BaseModel):
    tip_cod: int
    tip_denumire: str
    nivel: str  # "UAT" (localitati) sau "componenta" (localitati_componente, sate)


# Future ideas kept here for later implementation:
# - add lookup endpoints by postal code or alternative locality names if new sources are imported;
# - add fuzzy-search suggestions for misspelled locality names.


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
def list_judete(request: Request, conn: sqlite3.Connection = Depends(get_sqlite_connection)):
    rows = conn.execute("SELECT cod_judet, denumire FROM judete ORDER BY denumire").fetchall()
    return cached_json(request, [dict(r) for r in rows])

@router.get("/localitate/{cod}", response_model=LocalitateEntry, summary="Cauta localitate dupa cod SIRUTA")
@limiter.limit(_dynamic_limit)
def get_localitate_by_siruta(
    request: Request, 
    cod: int = Path(..., description="Codul unic SIRUTA (numeric)"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
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
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    # SQLite LIKE handles ASCII case-insensitively, but not Unicode case-folding for diacritics.
    # Search the ASCII column with a de-accented uppercase pattern and the diacritics column
    # with a lowercase Unicode-preserving pattern.
    query = q.strip()
    ascii_pattern = f"%{_strip_diacritics(query).upper()}%"
    diacritics_pattern = f"%{query.lower()}%"
    sql_where = " WHERE l.denumire LIKE ? OR l.denumire_diacritice LIKE ? "
    
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
    tip_cod: int | None = Query(None, description="Filtrare dupa tip ierarhie (ex: 12 pentru municipii)"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    query = _SIRUTA_BASE_QUERY + " WHERE l.cod_judet = ?"
    params = [cod_judet]
    
    if tip_cod is not None:
        query += " AND l.tip_cod = ?"
        params.append(tip_cod)
        
    query += " ORDER BY l.tip_cod ASC, l.denumire ASC"

    rows = conn.execute(query, tuple(params)).fetchall()
    return cached_json(request, [dict(r) for r in rows])


_JUDET_DETAIL_QUERY = """
    SELECT j.cod_judet, j.denumire, j.abbr, j.cod_regiune, r.denumire AS regiune_denumire, j.cod_siruta_judet
    FROM judete j
    LEFT JOIN regiuni r ON j.cod_regiune = r.cod_regiune
"""

@router.get("/judete/{cod_judet}", response_model=JudetDetail, summary="Detalii judet dupa cod")
@limiter.limit(_dynamic_limit)
def get_judet(
    request: Request,
    cod_judet: int = Path(..., description="Codul judetului (ex: 41 pentru Vrancea)"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    row = conn.execute(_JUDET_DETAIL_QUERY + " WHERE j.cod_judet = ?", (cod_judet,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Judetul cu codul {cod_judet} nu a fost gasit.")
    return cached_json(request, dict(row))

@router.get("/judete/abbr/{abbr}", response_model=JudetDetail, summary="Detalii judet dupa abrevierea auto")
@limiter.limit(_dynamic_limit)
def get_judet_by_abbr(
    request: Request,
    abbr: str = Path(..., description="Abrevierea auto a judetului (ex: CJ, MS, B)"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    row = conn.execute(_JUDET_DETAIL_QUERY + " WHERE UPPER(j.abbr) = UPPER(?)", (abbr,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Judetul cu abrevierea '{abbr}' nu a fost gasit.")
    return cached_json(request, dict(row))

@router.get("/regiuni", response_model=list[RegiuneEntry], summary="Toate regiunile de dezvoltare (NUTS2)")
@limiter.limit(_dynamic_limit)
def list_regiuni(request: Request, conn: sqlite3.Connection = Depends(get_sqlite_connection)):
    rows = conn.execute("SELECT cod_regiune, denumire, nuts2 FROM regiuni ORDER BY denumire").fetchall()
    return cached_json(request, [dict(r) for r in rows])

@router.get("/regiuni/{cod_regiune}/judete", response_model=list[Judet], summary="Judetele dintr-o regiune de dezvoltare")
@limiter.limit(_dynamic_limit)
def get_judete_by_regiune(
    request: Request,
    cod_regiune: int = Path(..., description="Codul regiunii de dezvoltare"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    exista = conn.execute("SELECT 1 FROM regiuni WHERE cod_regiune = ?", (cod_regiune,)).fetchone()
    if exista is None:
        raise HTTPException(status_code=404, detail=f"Regiunea cu codul {cod_regiune} nu a fost gasita.")
    rows = conn.execute(
        "SELECT cod_judet, denumire FROM judete WHERE cod_regiune = ? ORDER BY denumire",
        (cod_regiune,),
    ).fetchall()
    return cached_json(request, [dict(r) for r in rows])

@router.get(
    "/localitate/{cod}/componente",
    response_model=ComponenteResponse,
    summary="Localitati componente (sate) ale unui UAT",
    description=(
        "Localitatile componente (sate apartinatoare, localitati de baza etc.) ale unui UAT "
        "(municipiu, oras sau comuna), identificat dupa codul sau SIRUTA. `lat`/`lon` provin "
        "dintr-o potrivire nume+judet fata de `localitati_geo` (vezi "
        "scripts/match_localitati_geo_siruta.py) -- null cand potrivirea e ambigua sau lipseste. "
        "`coduri_postale` provine din tabela `coduri_postale` (poate fi o lista goala)."
    ),
)
@limiter.limit(_dynamic_limit)
def get_componente(
    request: Request,
    cod: int = Path(..., description="Codul SIRUTA al UAT-ului parinte"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    parinte_row = conn.execute(_SIRUTA_BASE_QUERY + " WHERE l.cod_siruta = ?", (cod,)).fetchone()
    if parinte_row is None:
        raise HTTPException(status_code=404, detail=f"Codul SIRUTA {cod} nu a fost gasit intre UAT-uri.")

    rows = conn.execute(
        """
        SELECT lc.cod_siruta, lc.denumire, lc.tip_cod, lc.tip_denumire, lc.cod_siruta_parinte,
               lc.cod_judet, j.denumire AS judet_denumire, lg.lat, lg.lon
        FROM localitati_componente lc
        JOIN judete j ON lc.cod_judet = j.cod_judet
        LEFT JOIN localitati_geo lg ON lg.cod_siruta = lc.cod_siruta
        WHERE lc.cod_siruta_parinte = ?
        ORDER BY lc.tip_cod, lc.denumire
        """,
        (cod,),
    ).fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"UAT-ul cu codul SIRUTA {cod} nu are localitati componente.",
        )

    cod_siruta_list = [r["cod_siruta"] for r in rows]
    placeholders = ",".join("?" for _ in cod_siruta_list)
    coduri_postale_by_siruta: dict[int, list[str]] = {}
    for cs, cp in conn.execute(
        f"SELECT DISTINCT cod_siruta, cod_postal FROM coduri_postale WHERE cod_siruta IN ({placeholders})",
        cod_siruta_list,
    ).fetchall():
        coduri_postale_by_siruta.setdefault(cs, []).append(cp)

    results = [
        {
            "cod_siruta": r["cod_siruta"],
            "denumire": r["denumire"],
            "tip_cod": r["tip_cod"],
            "tip_denumire": r["tip_denumire"],
            "cod_siruta_parinte": r["cod_siruta_parinte"],
            "cod_judet": r["cod_judet"],
            "judet_denumire": r["judet_denumire"],
            "lat": r["lat"],
            "lon": r["lon"],
            "coduri_postale": sorted(coduri_postale_by_siruta.get(r["cod_siruta"], [])),
        }
        for r in rows
    ]

    return cached_json(
        request,
        {"parinte": dict(parinte_row), "total": len(results), "results": results},
    )

@router.get("/tipuri", response_model=list[TipLocalitateEntry], summary="Nomenclator tipuri de localitati")
@limiter.limit(_dynamic_limit)
def list_tipuri(request: Request):
    from scripts.init_siruta_db import TIP_DENUMIRE, TIP_DENUMIRE_COMPONENTE

    entries = [
        {"tip_cod": int(tip_cod), "tip_denumire": denumire, "nivel": "UAT"}
        for tip_cod, denumire in TIP_DENUMIRE.items()
    ] + [
        {"tip_cod": tip_cod, "tip_denumire": denumire, "nivel": "componenta"}
        for tip_cod, denumire in TIP_DENUMIRE_COMPONENTE.items()
    ]
    return cached_json(request, entries)