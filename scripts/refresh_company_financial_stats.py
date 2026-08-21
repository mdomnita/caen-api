"""Rebuild company_financial_stats: precomputed count/sum/avg/min/max (NOT median --
see below) backing GET /companii/financiar/statistici, at three granularities computed
in one pass: national, per-judet, and per-caen.

Why this exists: on this deployment, company_financials (13M+ rows) sits on a Postgres
instance with very slow disk I/O, and the query planner prefers a full sequential scan
of the whole table even when filtering to a single year (an index on `an` exists but
isn't used -- see the investigation that led to this script). A live aggregate query
can take several minutes. This script pays that cost once, offline, and the endpoint
reads the tiny result table instead.

Single streamed pass, not one query per year/camp: re-scanning per (year, camp) would
mean dozens of full-table scans (hours), since the planner doesn't narrow by `an`
anyway. Instead this streams the whole table exactly once and accumulates count/sum/
min/max for every (an, camp, judet-or-None, caen-or-None) group in memory (bounded by
distinct group count, not row count -- cheap).

Median is intentionally NOT computed here: an exact streaming median needs either
unbounded per-group memory (every raw value) or an approximate algorithm, and neither
was worth the complexity for a first version. Precomputed rows always have
mediana=NULL. GET /companii/financiar/statistici still computes exact median live for
filter combinations that aren't precomputed (`localitate`, or `judet`+`caen` together).

Run after each scripts/import_company_financials.py or scripts/import_companies.py
import -- this table is otherwise silently stale (the endpoint falls back to a live
query only when there's no row at all for the requested (an, camp), so a stale-but-
present row would be served as-is).

Usage:
    python scripts/refresh_company_financial_stats.py
"""
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, insert, select

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import (
    FINANCIAL_COLUMNS,
    Company,
    CompanyFinancial,
    CompanyFinancialStats,
)


@dataclass
class _Accumulator:
    count: int = 0
    total: int = 0
    minimum: int | None = None
    maximum: int | None = None

    def add(self, value: int) -> None:
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)


_GroupKey = tuple[int, str, str | None, str | None]  # (an, camp, judet, caen)


def _accumulate(batch_size: int) -> dict[_GroupKey, _Accumulator]:
    accumulators: dict[_GroupKey, _Accumulator] = defaultdict(_Accumulator)
    columns = [getattr(CompanyFinancial, name) for name in FINANCIAL_COLUMNS]
    stmt = (
        select(CompanyFinancial.an, CompanyFinancial.caen, Company.county, *columns)
        .select_from(CompanyFinancial)
        .join(Company, Company.id == CompanyFinancial.company_id)
        .execution_options(yield_per=batch_size)
    )

    with SessionLocal() as session:
        for an, caen, judet, *values in session.execute(stmt):
            for camp, value in zip(FINANCIAL_COLUMNS, values):
                if value is None:
                    continue
                accumulators[(an, camp, None, None)].add(value)
                if judet:
                    accumulators[(an, camp, judet, None)].add(value)
                if caen:
                    accumulators[(an, camp, None, caen)].add(value)

    return accumulators


def refresh_company_financial_stats(batch_size: int = 10_000) -> int:
    """Recompute and replace company_financial_stats. Returns the row count written."""
    init_postgres()

    accumulators = _accumulate(batch_size)

    rows = [
        {
            "an": an,
            "camp": camp,
            "judet": judet,
            "caen": caen,
            "numar_firme": acc.count,
            "suma": acc.total,
            "medie": acc.total / acc.count if acc.count else None,
            "mediana": None,  # not computed -- see module docstring
            "minim": acc.minimum,
            "maxim": acc.maximum,
        }
        for (an, camp, judet, caen), acc in accumulators.items()
    ]

    with SessionLocal() as session:
        session.execute(delete(CompanyFinancialStats))
        if rows:
            session.execute(insert(CompanyFinancialStats), rows)
        session.commit()

    return len(rows)


def main() -> None:
    written = refresh_company_financial_stats()
    print(f"company_financial_stats reconstruit: {written} randuri (national + per-judet + per-caen).")


if __name__ == "__main__":
    main()
