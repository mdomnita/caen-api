from datetime import date as _Date
import sqlite3

from fastapi import APIRouter, Depends, Path, Query, Request, HTTPException
from pydantic import BaseModel

from auth import limiter, _dynamic_limit, cached_json
from api_dependencies import get_sqlite_connection

router = APIRouter(
    prefix="/schimb",
    tags=["Curs Valutar BNR"],
)

# ---------------------------------------------------------------------------
# Valute istorice
# ---------------------------------------------------------------------------
# Valute care nu mai sunt publicate de BNR (ex: dupa aderarea la zona euro).
# `ultima_data_activa` este ultima zi pentru care BNR a publicat un curs
# oficial; interogarile pe perioade care se extind dupa aceasta data sunt
# limitate automat la ea in loc sa returneze eroare de date lipsa (vezi
# `_clamp_perioada_istorica`), iar raspunsurile pentru valuta respectiva
# includ `istorica=True`.
OBSOLETE_CURRENCIES: dict[str, dict] = {
    "BGN": {
        "ultima_data_activa": "2025-12-31",
        "motiv": "Bulgaria a aderat la zona euro la 1 ianuarie 2026",
        "curs_fix_eur": 1.95583,  # paritate fixa istorica leva/euro (currency board din 1997)
    },
}


def _obsolete_info(valuta: str) -> dict | None:
    return OBSOLETE_CURRENCIES.get(valuta)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ValutaInfo(BaseModel):
    valuta: str
    ultima_data: str
    curs_unitar: float
    istorica: bool = False
    ultima_data_activa: str | None = None


class CursZi(BaseModel):
    data: str
    valuta: str
    curs: float
    multiplicator: int
    curs_unitar: float
    istorica: bool = False
    ultima_data_activa: str | None = None


class PerecheZi(BaseModel):
    data: str
    sursa: str
    destinatie: str
    curs: float
    sursa_istorica: bool = False
    destinatie_istorica: bool = False


class PunctEvolutie(BaseModel):
    data: str
    curs: float


class EvolutieResponse(BaseModel):
    sursa: str
    destinatie: str
    date_start: str
    date_end: str
    puncte: list[PunctEvolutie]
    sursa_istorica: bool = False
    destinatie_istorica: bool = False


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


def _clamp_perioada_istorica(valuta: str, start_iso: str, end_iso: str) -> tuple[str, str, bool]:
    """Clamp an [start, end] query window to a historical/obsolete currency's
    last published date, so a range reaching past it still returns the real
    data available instead of 404ing with "no data" for dates BNR will
    never publish (e.g. BGN after 2025-12-31, once Bulgaria adopted the
    euro). Non-obsolete currencies pass through unchanged.

    Returns (clamped_start, clamped_end, istorica).
    """
    meta = _obsolete_info(valuta)
    if meta is None:
        return start_iso, end_iso, False
    ultima_data_activa = meta["ultima_data_activa"]
    clamped_end = min(end_iso, ultima_data_activa)
    clamped_start = min(start_iso, clamped_end)
    return clamped_start, clamped_end, True


def _clamp_perioada_pereche(
    sursa: str, destinatie: str, start_iso: str, end_iso: str
) -> tuple[str, str, bool, bool]:
    """Same as _clamp_perioada_istorica, but for a pair where either side
    (or neither) may be historical. RON is never obsolete, so this composes
    cleanly whichever side it's on. If both sides are historical with
    different cutoffs, the earlier one wins (the pair can only have data
    while both currencies were still being published).

    Returns (clamped_start, clamped_end, sursa_istorica, destinatie_istorica).
    """
    sursa_istorica = sursa in OBSOLETE_CURRENCIES
    destinatie_istorica = destinatie in OBSOLETE_CURRENCIES
    clamped_end = end_iso
    if sursa_istorica:
        clamped_end = min(clamped_end, OBSOLETE_CURRENCIES[sursa]["ultima_data_activa"])
    if destinatie_istorica:
        clamped_end = min(clamped_end, OBSOLETE_CURRENCIES[destinatie]["ultima_data_activa"])
    clamped_start = min(start_iso, clamped_end)
    return clamped_start, clamped_end, sursa_istorica, destinatie_istorica


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
            "istorica": r["valuta"] in OBSOLETE_CURRENCIES,
            "ultima_data_activa": (OBSOLETE_CURRENCIES.get(r["valuta"]) or {}).get("ultima_data_activa"),
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
            "istorica": r["valuta"] in OBSOLETE_CURRENCIES,
            "ultima_data_activa": (OBSOLETE_CURRENCIES.get(r["valuta"]) or {}).get("ultima_data_activa"),
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
    meta = _obsolete_info(valuta)
    return cached_json(request, {
        "data": actual_date,
        "valuta": valuta,
        "curs": curs,
        "multiplicator": mult,
        "curs_unitar": round(curs / mult, 4),
        "istorica": meta is not None,
        "ultima_data_activa": meta["ultima_data_activa"] if meta else None,
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
    start_iso, end_iso, istorica = _clamp_perioada_istorica(valuta, start_iso, end_iso)
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
        "sursa_istorica": istorica,
        "destinatie_istorica": False,
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
    from_iso, to_iso, _istorica = _clamp_perioada_istorica(valuta, from_iso, to_iso)
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

    sursa_istorica = sursa in OBSOLETE_CURRENCIES
    destinatie_istorica = destinatie in OBSOLETE_CURRENCIES

    if sursa == destinatie:
        return cached_json(request, {
            "data": data_iso, "sursa": sursa, "destinatie": destinatie, "curs": 1.0,
            "sursa_istorica": sursa_istorica, "destinatie_istorica": destinatie_istorica,
        })

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
            "sursa_istorica": sursa_istorica,
            "destinatie_istorica": destinatie_istorica,
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
            "sursa_istorica": sursa_istorica,
            "destinatie_istorica": destinatie_istorica,
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
        "sursa_istorica": sursa_istorica,
        "destinatie_istorica": destinatie_istorica,
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
    start_iso, end_iso, sursa_istorica, destinatie_istorica = _clamp_perioada_pereche(
        sursa, destinatie, start_iso, end_iso
    )

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
        "sursa_istorica": sursa_istorica,
        "destinatie_istorica": destinatie_istorica,
    })
