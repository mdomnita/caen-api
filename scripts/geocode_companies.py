import argparse
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company
from services.geocoding import ArcGisProvider


DEFAULT_SLEEP = 0.1
DEFAULT_WORKERS = 8
MAX_COMMIT_RETRIES = 5
COMMIT_RETRY_BASE_DELAY = 1.0


def _is_retryable_db_error(exc: OperationalError) -> bool:
    """Deadlocks can happen when init_postgres()'s schema check/DDL (see
    company_database.py) races another process's in-flight UPDATE
    transaction on `companies` — e.g. two overlapping runs of this script,
    or the API restarting mid-batch. Retrying the same batch is always safe:
    a failed commit rolls back the whole batch (nothing partially applied),
    so the rows are still geocode_status IS NULL and _fetch_batch() will
    return the exact same batch again.
    """
    return "deadlock detected" in str(exc).lower()


@dataclass
class GeocodeStats:
    processed: int = 0
    ok: int = 0
    not_found: int = 0
    no_address: int = 0
    error: int = 0

    def merge(self, other: "GeocodeStats") -> None:
        self.processed += other.processed
        self.ok += other.ok
        self.not_found += other.not_found
        self.no_address += other.no_address
        self.error += other.error


def _build_address(company: Company) -> str | None:
    parts = [
        " ".join(p for p in [company.street, company.street_number] if p),
        company.locality,
        company.county,
        company.postal_code,
        company.country or "Romania",
    ]
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return None
    return ", ".join(parts)


def _geocode_task(company_id: int, address: str, provider: ArcGisProvider, sleep: float):
    if sleep:
        time.sleep(sleep)
    try:
        return company_id, provider.geocode(address), None
    except requests.RequestException as exc:
        # one retry (the provider itself rotates to the next proxy internally)
        try:
            time.sleep(max(sleep, 0.2))
            return company_id, provider.geocode(address), None
        except requests.RequestException as retry_exc:
            return company_id, None, retry_exc


def _pending_statuses(retry_failed: bool) -> list[str] | None:
    if not retry_failed:
        return None
    return ["error", "not_found"]


def _fetch_batch(session, batch_size: int, last_id: int, retry_failed: bool) -> list[Company]:
    statuses = _pending_statuses(retry_failed)
    stmt = select(Company).where(Company.id > last_id)
    if statuses is None:
        stmt = stmt.where(Company.geocode_status.is_(None))
    else:
        stmt = stmt.where(Company.geocode_status.in_(statuses))
    stmt = stmt.order_by(Company.id).limit(batch_size)
    return list(session.scalars(stmt))


def _process_batch(
    fetch_size: int,
    last_id: int,
    retry_failed: bool,
    dry_run: bool,
    executor: ThreadPoolExecutor,
    provider: ArcGisProvider,
    sleep: float,
) -> tuple[int, GeocodeStats] | None:
    """Fetch, geocode, and commit one batch. Returns (new_last_id, batch
    stats), or None if there were no more rows. Only ever called by
    geocode_companies() through the retry wrapper below — a failed commit
    raises out of the `with SessionLocal()` block, which rolls back
    everything in this batch (nothing partially applied), so retrying with
    the same last_id re-fetches and re-processes the identical rows.
    """
    with SessionLocal() as session:
        batch = _fetch_batch(session, fetch_size, last_id, retry_failed)
        if not batch:
            return None

        batch_stats = GeocodeStats()
        companies_by_id = {company.id: company for company in batch}
        todo: list[tuple[int, str]] = []
        new_last_id = last_id

        for company in batch:
            new_last_id = company.id
            batch_stats.processed += 1
            address = _build_address(company)
            if address is None:
                print(f"[{company.id}] fara adresa utilizabila - sarit")
                if not dry_run:
                    company.geocode_status = "no_address"
                    company.geocoded_at = datetime.now(timezone.utc)
                batch_stats.no_address += 1
                continue

            if dry_run:
                print(f"[{company.id}] {address}")
                continue

            todo.append((company.id, address))

        if todo:
            futures = [
                executor.submit(_geocode_task, company_id, address, provider, sleep)
                for company_id, address in todo
            ]
            for future in as_completed(futures):
                company_id, result, exc = future.result()
                company = companies_by_id[company_id]
                if exc is not None:
                    print(f"[{company_id}] eroare geocodare: {exc}")
                    company.geocode_status = "error"
                    company.geocoded_at = datetime.now(timezone.utc)
                    batch_stats.error += 1
                elif result is None:
                    print(f"[{company_id}] nicio potrivire")
                    company.geocode_status = "not_found"
                    company.geocoded_at = datetime.now(timezone.utc)
                    batch_stats.not_found += 1
                else:
                    company.latitude = round(result.lat, 6)
                    company.longitude = round(result.lon, 6)
                    company.geocode_score = result.score
                    company.geocode_status = "ok"
                    company.geocoded_at = datetime.now(timezone.utc)
                    batch_stats.ok += 1

        if not dry_run:
            session.commit()

    return new_last_id, batch_stats


def geocode_companies(
    batch_size: int = 200,
    limit: int | None = None,
    sleep: float = DEFAULT_SLEEP,
    workers: int = DEFAULT_WORKERS,
    retry_failed: bool = False,
    dry_run: bool = False,
    use_proxy: bool = True,
) -> GeocodeStats:
    init_postgres()

    provider = ArcGisProvider(use_proxy=use_proxy)
    stats = GeocodeStats()
    last_id = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        while limit is None or stats.processed < limit:
            remaining = None if limit is None else limit - stats.processed
            fetch_size = batch_size if remaining is None else min(batch_size, remaining)

            attempt = 0
            while True:
                try:
                    result = _process_batch(
                        fetch_size, last_id, retry_failed, dry_run, executor, provider, sleep,
                    )
                    break
                except OperationalError as exc:
                    if not _is_retryable_db_error(exc) or attempt >= MAX_COMMIT_RETRIES:
                        raise
                    attempt += 1
                    delay = COMMIT_RETRY_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                    print(
                        f"Eroare tranzitorie la commit (probabil deadlock), reincerc "
                        f"batch-ul ({attempt}/{MAX_COMMIT_RETRIES}) dupa {delay:.1f}s: {exc}"
                    )
                    time.sleep(delay)

            if result is None:
                break

            last_id, batch_stats = result
            stats.merge(batch_stats)

            print(
                f"Progres: {stats.processed} procesate "
                f"({stats.ok} ok, {stats.not_found} negasite, "
                f"{stats.no_address} fara adresa, {stats.error} erori)"
            )

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Geocodeaza firmele din PostgreSQL folosind serviciul gratuit ArcGIS "
        "(anonim, fara cheie API)."
    )
    parser.add_argument("--batch-size", type=int, default=200, help="Numarul de randuri per batch")
    parser.add_argument("--limit", type=int, default=None, help="Numarul maxim de randuri de procesat in aceasta rulare")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP, help="Pauza (secunde) inainte de fiecare cerere catre ArcGIS")
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS, help="Numarul de cereri de geocodare in paralel"
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Nu folosi proxy-urile din scripts/proxies.py, cere direct catre ArcGIS",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Reincearca si randurile cu status 'error' sau 'not_found', nu doar cele negeocodate",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Afiseaza adresele care ar fi geocodate, fara a apela ArcGIS sau a scrie in baza de date",
    )
    args = parser.parse_args()

    stats = geocode_companies(
        batch_size=args.batch_size,
        limit=args.limit,
        sleep=args.sleep,
        workers=args.workers,
        retry_failed=args.retry_failed,
        dry_run=args.dry_run,
        use_proxy=not args.no_proxy,
    )
    print("Geocodare finalizata.")
    print(f"Procesate: {stats.processed}")
    print(f"Ok: {stats.ok}")
    print(f"Negasite: {stats.not_found}")
    print(f"Fara adresa: {stats.no_address}")
    print(f"Erori: {stats.error}")


if __name__ == "__main__":
    main()
