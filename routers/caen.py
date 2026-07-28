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


_TIPURI_CORESPONDENTA = (
    "NESCHIMBAT", "RECODIFICARE", "DETALIERE", "AGREGARE", "MIXT", "NOU",
)


class CorespondentaItem(BaseModel):
    cod_v3: str
    denumire_v3: str
    tip_corespondenta: str


class CAENv2Detail(BaseModel):
    cod: str
    denumire: str
    corespondente: list[CorespondentaItem]


class CorespondentaV2Item(BaseModel):
    cod_v2: str | None
    denumire_v2: str | None
    tip_corespondenta: str


class CAENv3Predecesori(BaseModel):
    cod: str
    denumire: str
    predecesori: list[CorespondentaV2Item]


class CorespondentaFullItem(BaseModel):
    id: int
    cod_v2: str | None
    denumire_v2: str | None
    cod_v3: str
    denumire_v3: str
    tip_corespondenta: str


class CorespondentaSearchResponse(BaseModel):
    total: int
    results: list[CorespondentaFullItem]


@router.get(
    "/corespondenta",
    response_model=CorespondentaSearchResponse,
    summary="Cauta corespondente CAEN v2 <-> v3 dupa cod si/sau tip",
)
@limiter.limit(_dynamic_limit)
def search_corespondenta(
    request: Request,
    v2: str | None = Query(None, pattern=r"^\d{2,4}$", description="Cod CAEN v2 (2-4 cifre)"),
    v3: str | None = Query(None, pattern=r"^\d{2,4}$", description="Cod CAEN v3 (2-4 cifre)"),
    tip: str | None = Query(None, description="Filtreaza dupa tip_corespondenta"),
    limit: int = Query(50, ge=1, le=200, description="Numar maxim de rezultate"),
    offset: int = Query(0, ge=0, description="Paginare – pozitia de start"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    """
    Cauta corespondente CAEN v2 <-> v3. Trebuie specificat cel putin unul
    dintre parametrii `v2` sau `v3`; optional se poate filtra si dupa `tip`.
    Exemplu: `/caen/corespondenta?v3=4792` sau `/caen/corespondenta?v2=0119`
    """
    if v2 is None and v3 is None:
        raise HTTPException(
            status_code=422,
            detail="Trebuie specificat cel putin unul dintre parametrii 'v2' sau 'v3'.",
        )

    if tip is not None and tip not in _TIPURI_CORESPONDENTA:
        raise HTTPException(
            status_code=422,
            detail=f"Tip corespondenta invalid: '{tip}'. Valori acceptate: {', '.join(_TIPURI_CORESPONDENTA)}.",
        )

    conditions = []
    params: list[str] = []
    if v2 is not None:
        conditions.append("co.cod_v2 = ?")
        params.append(v2.strip())
    if v3 is not None:
        conditions.append("co.cod_v3 = ?")
        params.append(v3.strip())
    if tip is not None:
        conditions.append("co.tip_corespondenta = ?")
        params.append(tip)

    sql_where = " WHERE " + " AND ".join(conditions)
    sql_from = """
        FROM   caen_corespondenta co
        LEFT   JOIN caen_v2 v2c ON co.cod_v2 = v2c.cod
        JOIN   clase        v3c ON co.cod_v3 = v3c.cod
    """

    total = conn.execute(
        f"SELECT COUNT(*) {sql_from} {sql_where}", params
    ).fetchone()[0]

    rows = conn.execute(
        f"""
        SELECT
            co.id                AS id,
            co.cod_v2            AS cod_v2,
            v2c.denumire         AS denumire_v2,
            co.cod_v3            AS cod_v3,
            v3c.denumire         AS denumire_v3,
            co.tip_corespondenta AS tip_corespondenta
        {sql_from} {sql_where}
        ORDER BY co.id
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()

    return cached_json(request, {"total": total, "results": [dict(r) for r in rows]})


@router.get(
    "/v2/{cod}",
    response_model=CAENv2Detail,
    summary="Detalii clasa CAEN v2 si corespondentele ei v3",
)
@limiter.limit(_dynamic_limit)
def get_v2_by_code(
    request: Request,
    cod: str = Path(pattern=r"^\d{2,4}$", description="Cod CAEN v2 (2-4 cifre)"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    """
    Returneaza denumirea unei clase CAEN Rev.2 impreuna cu toate
    corespondentele ei catre clase CAEN Rev.3.
    """
    v2_row = conn.execute(
        "SELECT cod, denumire FROM caen_v2 WHERE cod = ?", (cod.strip(),)
    ).fetchone()

    if v2_row is None:
        raise HTTPException(status_code=404, detail=f"Codul CAEN v2 '{cod}' nu a fost gasit.")

    corespondente = conn.execute(
        """
        SELECT
            co.cod_v3            AS cod_v3,
            c.denumire           AS denumire_v3,
            co.tip_corespondenta AS tip_corespondenta
        FROM   caen_corespondenta co
        JOIN   clase c ON co.cod_v3 = c.cod
        WHERE  co.cod_v2 = ?
        ORDER BY co.cod_v3
        """,
        (cod.strip(),),
    ).fetchall()

    return cached_json(request, {
        "cod": v2_row["cod"],
        "denumire": v2_row["denumire"],
        "corespondente": [dict(r) for r in corespondente],
    })


@router.get(
    "/v3/{cod}/v2",
    response_model=CAENv3Predecesori,
    summary="Codurile CAEN v2 din care provine un cod CAEN v3",
)
@limiter.limit(_dynamic_limit)
def get_v3_predecesori(
    request: Request,
    cod: str = Path(pattern=r"^\d{2,4}$", description="Cod CAEN v3 (2-4 cifre)"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    """
    Returneaza codul CAEN v3 si lista de coduri CAEN v2 din care provine
    (corespondenta inversa). Pentru clasele noi in Rev.3 (`tip_corespondenta`
    = `NOU`), `cod_v2` si `denumire_v2` sunt `null`.
    """
    v3_row = conn.execute(
        "SELECT cod, denumire FROM clase WHERE cod = ?", (cod.strip(),)
    ).fetchone()

    if v3_row is None:
        raise HTTPException(status_code=404, detail=f"Codul CAEN '{cod}' nu a fost gasit.")

    predecesori = conn.execute(
        """
        SELECT
            co.cod_v2            AS cod_v2,
            v2.denumire          AS denumire_v2,
            co.tip_corespondenta AS tip_corespondenta
        FROM   caen_corespondenta co
        LEFT   JOIN caen_v2 v2 ON co.cod_v2 = v2.cod
        WHERE  co.cod_v3 = ?
        ORDER BY co.cod_v2
        """,
        (cod.strip(),),
    ).fetchall()

    return cached_json(request, {
        "cod": v3_row["cod"],
        "denumire": v3_row["denumire"],
        "predecesori": [dict(r) for r in predecesori],
    })


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
