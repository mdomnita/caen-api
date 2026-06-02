from datetime import date as _Date

from fastapi import APIRouter, Path, Query, Request, HTTPException, Security
from pydantic import BaseModel

from auth import get_db, limiter, _dynamic_limit, cached_json, get_api_key

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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/valute",
    response_model=list[ValutaInfo],
    summary="Lista valutelor disponibile cu ultimul curs fata de RON",
)
@limiter.limit(_dynamic_limit)
def list_valute(request: Request):
    with get_db() as conn:
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
            "curs_unitar": round(r["curs"] / r["multiplicator"], 6),
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
    data: str = Path(..., description="Data in format YYYY-MM-DD"),
):
    valuta = valuta.upper()
    with get_db() as conn:
        res = _nearest(conn, valuta, data)
    if res is None:
        raise HTTPException(status_code=404, detail=f"Nu exista curs pentru {valuta} la sau inainte de {data}.")
    curs, mult, actual_date = res
    return cached_json(request, {
        "data": actual_date,
        "valuta": valuta,
        "curs": curs,
        "multiplicator": mult,
        "curs_unitar": round(curs / mult, 6),
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
    start: str = Query(..., description="Data de inceput YYYY-MM-DD"),
    end: str = Query(None, description="Data de sfarsit YYYY-MM-DD (implicit: azi)"),
):
    valuta = valuta.upper()
    if end is None:
        end = str(_Date.today())
    with get_db() as conn:
        rows = conn.execute(
            "SELECT data, curs, multiplicator FROM cursuri_valutare "
            "WHERE valuta = ? AND data BETWEEN ? AND ? ORDER BY data",
            (valuta, start, end),
        ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Nu exista date pentru {valuta} in intervalul {start} — {end}.")
    puncte = [{"data": r["data"], "curs": round(r["curs"] / r["multiplicator"], 6)} for r in rows]
    return cached_json(request, {
        "sursa": valuta,
        "destinatie": "RON",
        "date_start": start,
        "date_end": end,
        "puncte": puncte,
    })


@router.get(
    "/pereche/{sursa}/{destinatie}/{data}",
    response_model=PerecheZi,
    summary="Curs incrucist intre doua valute pe o zi specifica (via RON)",
)
@limiter.limit(_dynamic_limit)
def get_pereche(
    request: Request,
    sursa: str = Path(..., description="Valuta sursa (ex: EUR). Folositi RON pentru moneda nationala."),
    destinatie: str = Path(..., description="Valuta destinatie (ex: USD)."),
    data: str = Path(..., description="Data in format YYYY-MM-DD"),
):
    sursa = sursa.upper()
    destinatie = destinatie.upper()

    if sursa == destinatie:
        return cached_json(request, {"data": data, "sursa": sursa, "destinatie": destinatie, "curs": 1.0})

    with get_db() as conn:
        if sursa == "RON":
            res = _nearest(conn, destinatie, data)
            if res is None:
                raise HTTPException(status_code=404, detail=f"Nu exista curs pentru {destinatie} la sau inainte de {data}.")
            curs, mult, actual_date = res
            return cached_json(request, {
                "data": actual_date,
                "sursa": sursa,
                "destinatie": destinatie,
                "curs": round(mult / curs, 6),
            })

        if destinatie == "RON":
            res = _nearest(conn, sursa, data)
            if res is None:
                raise HTTPException(status_code=404, detail=f"Nu exista curs pentru {sursa} la sau inainte de {data}.")
            curs, mult, actual_date = res
            return cached_json(request, {
                "data": actual_date,
                "sursa": sursa,
                "destinatie": destinatie,
                "curs": round(curs / mult, 6),
            })

        res_s = _nearest(conn, sursa, data)
        res_d = _nearest(conn, destinatie, data)

    if res_s is None:
        raise HTTPException(status_code=404, detail=f"Nu exista curs pentru {sursa} la sau inainte de {data}.")
    if res_d is None:
        raise HTTPException(status_code=404, detail=f"Nu exista curs pentru {destinatie} la sau inainte de {data}.")

    rate_s = res_s[0] / res_s[1]
    rate_d = res_d[0] / res_d[1]
    actual_date = max(res_s[2], res_d[2])  # latest of the two actual dates used
    return cached_json(request, {
        "data": actual_date,
        "sursa": sursa,
        "destinatie": destinatie,
        "curs": round(rate_s / rate_d, 6),
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
    start: str = Query(..., description="Data de inceput YYYY-MM-DD"),
    end: str = Query(None, description="Data de sfarsit YYYY-MM-DD (implicit: azi)"),
):
    sursa = sursa.upper()
    destinatie = destinatie.upper()
    if end is None:
        end = str(_Date.today())

    with get_db() as conn:
        if sursa == "RON":
            rows = conn.execute(
                "SELECT data, curs, multiplicator FROM cursuri_valutare "
                "WHERE valuta = ? AND data BETWEEN ? AND ? ORDER BY data",
                (destinatie, start, end),
            ).fetchall()
            if not rows:
                raise HTTPException(status_code=404, detail=f"Nu exista date pentru {destinatie} in intervalul {start} — {end}.")
            puncte = [{"data": r["data"], "curs": round(r["multiplicator"] / r["curs"], 6)} for r in rows]

        elif destinatie == "RON":
            rows = conn.execute(
                "SELECT data, curs, multiplicator FROM cursuri_valutare "
                "WHERE valuta = ? AND data BETWEEN ? AND ? ORDER BY data",
                (sursa, start, end),
            ).fetchall()
            if not rows:
                raise HTTPException(status_code=404, detail=f"Nu exista date pentru {sursa} in intervalul {start} — {end}.")
            puncte = [{"data": r["data"], "curs": round(r["curs"] / r["multiplicator"], 6)} for r in rows]

        else:
            rows = conn.execute("""
                SELECT a.data,
                       (a.curs / a.multiplicator) / (b.curs / b.multiplicator) AS curs
                FROM cursuri_valutare a
                JOIN cursuri_valutare b ON a.data = b.data
                WHERE a.valuta = ? AND b.valuta = ?
                  AND a.data BETWEEN ? AND ?
                ORDER BY a.data
            """, (sursa, destinatie, start, end)).fetchall()
            if not rows:
                raise HTTPException(status_code=404, detail=f"Nu exista date pentru {sursa}/{destinatie} in intervalul {start} — {end}.")
            puncte = [{"data": r["data"], "curs": round(r["curs"], 6)} for r in rows]

    return cached_json(request, {
        "sursa": sursa,
        "destinatie": destinatie,
        "date_start": start,
        "date_end": end,
        "puncte": puncte,
    })
