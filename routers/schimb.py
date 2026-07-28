from datetime import date as _Date
import sqlite3

from fastapi import APIRouter, Depends, Path, Query, Request, HTTPException, Security
from pydantic import BaseModel

from auth import limiter, _dynamic_limit, cached_json, get_api_key
from api_dependencies import get_sqlite_connection

router = APIRouter(
    prefix="/schimb",
    tags=["Curs Valutar BNR"],
    dependencies=[Security(get_api_key)],
)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ValutaInfo(BaseModel):
    valuta: str
    ultima_data: str
    curs_unitar: float


class CursZi(BaseModel):
    data: str
    valuta: str
    curs: float
    multiplicator: int
    curs_unitar: float


class PerecheZi(BaseModel):
    data: str
    sursa: str
    destinatie: str
    curs: float


class PunctEvolutie(BaseModel):
    data: str
    curs: float


class EvolutieResponse(BaseModel):
    sursa: str
    destinatie: str
    date_start: str
    date_end: str
    puncte: list[PunctEvolutie]


# Future ideas kept here for later implementation:
# - add variation endpoints with delta and percentage change versus previous day, week, or month;
# - expose volatility/min-max summaries for a currency or pair over a period;
# - add a convert endpoint for concrete amounts, not only rates;
# - add weekend/holiday-aware snapshots that annotate whether the returned rate is exact or rolled from the prior trading day.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nearest(conn, valuta: str, data: str):
    """Return (curs, multiplicator, actual_date) using the closest prior trading day."""
    row = conn.execute(
        "SELECT curs, multiplicator, data FROM cursuri_valutare "
        "WHERE valuta = ? AND data <= ? ORDER BY data DESC LIMIT 1",
        (valuta, data),
    ).fetchone()
    return (row["curs"], row["multiplicator"], row["data"]) if row else None


def _ensure_not_future(*dates: _Date) -> None:
    today = _Date.today()
    if any(value > today for value in dates):
        raise HTTPException(status_code=422, detail="Data nu poate fi in viitor.")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/valute",
    response_model=list[ValutaInfo],
    summary="Lista valutelor disponibile cu ultimul curs fata de RON",
)
@limiter.limit(_dynamic_limit)
def list_valute(request: Request, conn: sqlite3.Connection = Depends(get_sqlite_connection)):
    rows = conn.execute("""
        SELECT valuta, data AS ultima_data, curs, multiplicator
        FROM cursuri_valutare
        WHERE (valuta, data) IN (
            SELECT valuta, MAX(data) FROM cursuri_valutare GROUP BY valuta
        )
        ORDER BY valuta
    """).fetchall()
    result = [
        {
            "valuta": r["valuta"],
            "ultima_data": r["ultima_data"],
            "curs_unitar": round(r["curs"] / r["multiplicator"], 4),
        }
        for r in rows
    ]
    return cached_json(request, result)


@router.get(
    "/valute/{data}",
    response_model=list[CursZi],
    summary="Lista valutelor disponibile cu cursul fata de RON la o data specifica",
)
@limiter.limit(_dynamic_limit)
def list_valute_la_data(
    request: Request,
    data: _Date = Path(..., description="Data in format YYYY-MM-DD"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    _ensure_not_future(data)
    data_iso = data.isoformat()
    rows = conn.execute(
        """
        SELECT cv.valuta, cv.data, cv.curs, cv.multiplicator
        FROM cursuri_valutare cv
        JOIN (
            SELECT valuta, MAX(data) AS data
            FROM cursuri_valutare
            WHERE data <= ?
            GROUP BY valuta
        ) latest ON latest.valuta = cv.valuta AND latest.data = cv.data
        ORDER BY cv.valuta
        """,
        (data_iso,),
    ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Nu exista cursuri valutare la sau inainte de {data_iso}.")
    result = [
        {
            "data": r["data"],
            "valuta": r["valuta"],
            "curs": r["curs"],
            "multiplicator": r["multiplicator"],
            "curs_unitar": round(r["curs"] / r["multiplicator"], 4),
        }
        for r in rows
    ]
    return cached_json(request, result)


@router.get(
    "/curs/{valuta}/{data}",
    response_model=CursZi,
    summary="Curs valutar fata de RON pe o zi specifica (sau ultima zi lucratoare anterioara)",
)
@limiter.limit(_dynamic_limit)
def get_curs(
    request: Request,
    valuta: str = Path(..., description="Cod valutar ISO 4217 (ex: EUR, USD, GBP)"),
    data: _Date = Path(..., description="Data in format YYYY-MM-DD"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    valuta = valuta.upper()
    _ensure_not_future(data)
    data_iso = data.isoformat()
    res = _nearest(conn, valuta, data_iso)
    if res is None:
        raise HTTPException(status_code=404, detail=f"Nu exista curs pentru {valuta} la sau inainte de {data_iso}.")
    curs, mult, actual_date = res
    return cached_json(request, {
        "data": actual_date,
        "valuta": valuta,
        "curs": curs,
        "multiplicator": mult,
        "curs_unitar": round(curs / mult, 4),
    })


@router.get(
    "/evolutie/{valuta}",
    response_model=EvolutieResponse,
    summary="Evolutia cursului unei valute fata de RON intr-o perioada",
)
@limiter.limit(_dynamic_limit)
def get_evolutie(
    request: Request,
    valuta: str = Path(..., description="Cod valutar ISO 4217 (ex: EUR)"),
    start: _Date = Query(..., description="Data de inceput YYYY-MM-DD"),
    end: _Date | None = Query(None, description="Data de sfarsit YYYY-MM-DD (implicit: azi)"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    valuta = valuta.upper()
    start_iso = start.isoformat()
    end_date = end or _Date.today()
    _ensure_not_future(start, end_date)
    end_iso = end_date.isoformat()
    rows = conn.execute(
        "SELECT data, curs, multiplicator FROM cursuri_valutare "
        "WHERE valuta = ? AND data BETWEEN ? AND ? ORDER BY data",
        (valuta, start_iso, end_iso),
    ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Nu exista date pentru {valuta} in intervalul {start_iso} — {end_iso}.")
    puncte = [{"data": r["data"], "curs": round(r["curs"] / r["multiplicator"], 4)} for r in rows]
    return cached_json(request, {
        "sursa": valuta,
        "destinatie": "RON",
        "date_start": start_iso,
        "date_end": end_iso,
        "puncte": puncte,
    })


@router.get(
    "/istoric/{valuta}",
    response_model=list[PunctEvolutie],
    summary="Istoricul cursului unei valute fata de RON intr-o perioada (interval obligatoriu)",
)
@limiter.limit(_dynamic_limit)
def get_istoric(
    request: Request,
    valuta: str = Path(..., description="Cod valutar ISO 4217 (ex: EUR)"),
    date_from: _Date = Query(..., alias="from", description="Data de inceput YYYY-MM-DD"),
    date_to: _Date = Query(..., alias="to", description="Data de sfarsit YYYY-MM-DD"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    valuta = valuta.upper()
    _ensure_not_future(date_from, date_to)
    from_iso = date_from.isoformat()
    to_iso = date_to.isoformat()
    rows = conn.execute(
        "SELECT data, curs, multiplicator FROM cursuri_valutare "
        "WHERE valuta = ? AND data BETWEEN ? AND ? ORDER BY data",
        (valuta, from_iso, to_iso),
    ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Nu exista date pentru {valuta} in intervalul {from_iso} — {to_iso}.")
    puncte = [{"data": r["data"], "curs": round(r["curs"] / r["multiplicator"], 4)} for r in rows]
    return cached_json(request, puncte)


@router.get(
    "/pereche/{sursa}/{destinatie}/{data}",
    response_model=PerecheZi,
    summary="Curs incrucisat intre doua valute pe o zi specifica (via RON)",
)
@limiter.limit(_dynamic_limit)
def get_pereche(
    request: Request,
    sursa: str = Path(..., description="Valuta sursa (ex: EUR). Folositi RON pentru moneda nationala."),
    destinatie: str = Path(..., description="Valuta destinatie (ex: USD)."),
    data: _Date = Path(..., description="Data in format YYYY-MM-DD"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    sursa = sursa.upper()
    destinatie = destinatie.upper()
    _ensure_not_future(data)
    data_iso = data.isoformat()

    if sursa == destinatie:
        return cached_json(request, {"data": data_iso, "sursa": sursa, "destinatie": destinatie, "curs": 1.0})

    if sursa == "RON":
        res = _nearest(conn, destinatie, data_iso)
        if res is None:
            raise HTTPException(status_code=404, detail=f"Nu exista curs pentru {destinatie} la sau inainte de {data_iso}.")
        curs, mult, actual_date = res
        return cached_json(request, {
            "data": actual_date,
            "sursa": sursa,
            "destinatie": destinatie,
            "curs": round(mult / curs, 4),
        })

    if destinatie == "RON":
        res = _nearest(conn, sursa, data_iso)
        if res is None:
            raise HTTPException(status_code=404, detail=f"Nu exista curs pentru {sursa} la sau inainte de {data_iso}.")
        curs, mult, actual_date = res
        return cached_json(request, {
            "data": actual_date,
            "sursa": sursa,
            "destinatie": destinatie,
            "curs": round(curs / mult, 4),
        })

    res_s = _nearest(conn, sursa, data_iso)
    res_d = _nearest(conn, destinatie, data_iso)

    if res_s is None:
        raise HTTPException(status_code=404, detail=f"Nu exista curs pentru {sursa} la sau inainte de {data_iso}.")
    if res_d is None:
        raise HTTPException(status_code=404, detail=f"Nu exista curs pentru {destinatie} la sau inainte de {data_iso}.")

    rate_s = res_s[0] / res_s[1]
    rate_d = res_d[0] / res_d[1]
    actual_date = max(res_s[2], res_d[2])  # latest of the two actual dates used
    return cached_json(request, {
        "data": actual_date,
        "sursa": sursa,
        "destinatie": destinatie,
        "curs": round(rate_s / rate_d, 4),
    })


@router.get(
    "/evolutie/pereche/{sursa}/{destinatie}",
    response_model=EvolutieResponse,
    summary="Evolutia cursului incrucist intre doua valute intr-o perioada (via RON)",
)
@limiter.limit(_dynamic_limit)
def get_evolutie_pereche(
    request: Request,
    sursa: str = Path(..., description="Valuta sursa (ex: EUR)"),
    destinatie: str = Path(..., description="Valuta destinatie (ex: USD)"),
    start: _Date = Query(..., description="Data de inceput YYYY-MM-DD"),
    end: _Date | None = Query(None, description="Data de sfarsit YYYY-MM-DD (implicit: azi)"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    sursa = sursa.upper()
    destinatie = destinatie.upper()
    start_iso = start.isoformat()
    end_date = end or _Date.today()
    _ensure_not_future(start, end_date)
    end_iso = end_date.isoformat()

    if sursa == "RON":
        rows = conn.execute(
            "SELECT data, curs, multiplicator FROM cursuri_valutare "
            "WHERE valuta = ? AND data BETWEEN ? AND ? ORDER BY data",
            (destinatie, start_iso, end_iso),
        ).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail=f"Nu exista date pentru {destinatie} in intervalul {start_iso} — {end_iso}.")
        puncte = [{"data": r["data"], "curs": round(r["multiplicator"] / r["curs"], 4)} for r in rows]

    elif destinatie == "RON":
        rows = conn.execute(
            "SELECT data, curs, multiplicator FROM cursuri_valutare "
            "WHERE valuta = ? AND data BETWEEN ? AND ? ORDER BY data",
            (sursa, start_iso, end_iso),
        ).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail=f"Nu exista date pentru {sursa} in intervalul {start_iso} — {end_iso}.")
        puncte = [{"data": r["data"], "curs": round(r["curs"] / r["multiplicator"], 4)} for r in rows]

    else:
        rows = conn.execute("""
            SELECT a.data,
                   (a.curs / a.multiplicator) / (b.curs / b.multiplicator) AS curs
            FROM cursuri_valutare a
            JOIN cursuri_valutare b ON a.data = b.data
            WHERE a.valuta = ? AND b.valuta = ?
              AND a.data BETWEEN ? AND ?
            ORDER BY a.data
        """, (sursa, destinatie, start_iso, end_iso)).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail=f"Nu exista date pentru {sursa}/{destinatie} in intervalul {start_iso} — {end_iso}.")
        puncte = [{"data": r["data"], "curs": round(r["curs"], 4)} for r in rows]

    return cached_json(request, {
        "sursa": sursa,
        "destinatie": destinatie,
        "date_start": start_iso,
        "date_end": end_iso,
        "puncte": puncte,
    })
