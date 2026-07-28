from datetime import date as _Date, timedelta
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel

from auth import _dynamic_limit, cached_json, limiter
from api_dependencies import get_sqlite_connection

router = APIRouter(
    prefix="/zilelibere",
    tags=["Zile Libere Legale"],
)


class ZiLibera(BaseModel):
    data: str
    zi_saptamana: str
    denumire_sarbatoare: str
    temei_art_139_codul_muncii: str
    cade_in_weekend: bool
    observatii: str | None
    sursa_legala: str
    sursa_calendar: str | None
    sursa_verificare_suplimentara: str | None


class PunteRecommendation(BaseModel):
    interval_start: str
    interval_end: str
    zile_libere_totale: int
    zile_concediu_necesare: int
    zile_concediu: list[str]
    zile_libere_legale: list[str]


def _serialize_row(row) -> dict:
    payload = dict(row)
    payload["cade_in_weekend"] = bool(payload["cade_in_weekend"])
    return payload


def _base_query() -> str:
    return """
        SELECT
            data,
            zi_saptamana,
            denumire_sarbatoare,
            temei_art_139_codul_muncii,
            cade_in_weekend,
            observatii,
            sursa_legala,
            sursa_calendar,
            sursa_verificare_suplimentara
        FROM zile_libere
    """


def _fetch_zile_libere(
    conn: sqlite3.Connection,
    start: _Date | None = None,
    end: _Date | None = None,
    month: int | None = None,
) -> list[dict]:
    query = _base_query()
    clauses = []
    params: list[object] = []

    if start:
        clauses.append("data >= ?")
        params.append(start.isoformat())
    if end:
        clauses.append("data <= ?")
        params.append(end.isoformat())
    if month is not None:
        clauses.append("CAST(strftime('%m', data) AS INTEGER) = ?")
        params.append(month)

    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    query += " ORDER BY data ASC, denumire_sarbatoare ASC"

    rows = conn.execute(query, tuple(params)).fetchall()

    return [_serialize_row(row) for row in rows]


def _non_working_blocks(year: int, holidays: set[_Date]) -> list[tuple[_Date, _Date]]:
    cursor = _Date(year, 1, 1)
    end_of_year = _Date(year, 12, 31)
    blocks: list[tuple[_Date, _Date]] = []

    while cursor <= end_of_year:
        is_non_working = cursor in holidays or cursor.weekday() >= 5
        if not is_non_working:
            cursor += timedelta(days=1)
            continue

        start = cursor
        while cursor <= end_of_year and (cursor in holidays or cursor.weekday() >= 5):
            cursor += timedelta(days=1)
        blocks.append((start, cursor - timedelta(days=1)))

    return blocks


def _recommend_punti(conn: sqlite3.Connection, max_zile_concediu: int, min_zile_libere: int) -> list[dict]:
    holidays = _fetch_zile_libere(conn)
    holiday_dates = {_Date.fromisoformat(item["data"]) for item in holidays}
    holiday_dates_by_year: dict[int, set[_Date]] = {}
    for holiday_date in holiday_dates:
        holiday_dates_by_year.setdefault(holiday_date.year, set()).add(holiday_date)

    recommendations: list[dict] = []

    for year, year_holidays in sorted(holiday_dates_by_year.items()):
        blocks = _non_working_blocks(year, year_holidays)
        for left_block, right_block in zip(blocks, blocks[1:]):
            gap_dates: list[_Date] = []
            cursor = left_block[1] + timedelta(days=1)
            while cursor < right_block[0]:
                gap_dates.append(cursor)
                cursor += timedelta(days=1)

            if not gap_dates or len(gap_dates) > max_zile_concediu:
                continue

            total_days = (right_block[1] - left_block[0]).days + 1
            if total_days < min_zile_libere:
                continue

            interval_holidays = sorted(
                holiday.isoformat()
                for holiday in year_holidays
                if left_block[0] <= holiday <= right_block[1]
            )
            recommendations.append(
                {
                    "interval_start": left_block[0].isoformat(),
                    "interval_end": right_block[1].isoformat(),
                    "zile_libere_totale": total_days,
                    "zile_concediu_necesare": len(gap_dates),
                    "zile_concediu": [item.isoformat() for item in gap_dates],
                    "zile_libere_legale": interval_holidays,
                }
            )

    return recommendations


# Future ideas kept here for later implementation:
# - add month/year filters to /zilelibere/punti when annual data becomes available;
# - score each recommendation by efficiency, e.g. more free days for fewer vacation days;
# - expose a mini-vacanta endpoint that can merge multiple nearby gaps, not only two adjacent non-working blocks;
# - add a workday endpoint that answers whether a specific date is working, holiday, or weekend.


@router.get(
    "/luna/{luna}",
    response_model=list[ZiLibera],
    summary="Zilele libere legale pentru o anumita luna",
)
@limiter.limit(_dynamic_limit)
def list_zile_libere_luna(
    request: Request,
    luna: int = Path(..., ge=1, le=12, description="Luna numerica, intre 1 si 12"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    return cached_json(request, _fetch_zile_libere(conn, month=luna))


@router.get(
    "/punti",
    response_model=list[PunteRecommendation],
    summary="Recomandari de punti bazate pe sarbatori legale si weekenduri",
)
@limiter.limit(_dynamic_limit)
def list_punti(
    request: Request,
    max_zile_concediu: int = Query(2, ge=1, le=10, description="Numarul maxim de zile de concediu propuse"),
    min_zile_libere: int = Query(4, ge=3, le=31, description="Numarul minim de zile libere consecutive recomandate"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    return cached_json(request, _recommend_punti(conn, max_zile_concediu, min_zile_libere))


@router.get(
    "",
    response_model=list[ZiLibera],
    summary="Zilele libere legale din Romania pentru intregul an sau pentru o perioada",
)
@limiter.limit(_dynamic_limit)
def list_zile_libere(
    request: Request,
    start: _Date | None = Query(None, description="Data de inceput YYYY-MM-DD"),
    end: _Date | None = Query(None, description="Data de sfarsit YYYY-MM-DD"),
    conn: sqlite3.Connection = Depends(get_sqlite_connection),
):
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="Parametrul start trebuie sa fie mai mic sau egal cu end.")
    return cached_json(request, _fetch_zile_libere(conn, start=start, end=end))