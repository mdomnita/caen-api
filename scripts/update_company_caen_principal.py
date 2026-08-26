"""Deriva CompanyCaenCode.is_principal din ANAF PlatitorTvaRest v9 (live, gratuit, fara auth).

Exportul bulk ONRC (od_caen_autorizat.csv, importat de import_company_caen.py) nu marcheaza
niciunde care cod CAEN autorizat e cel principal -- randurile sunt doar sortate numeric. ANAF
intoarce insa un singur cod CAEN principal autoritar per CUI, printr-un apel live (batch de pana
la 500 CUI/request). Vezi DATABASE_SETUP.md §2.3.

Actualizeaza DOAR coloana is_principal pe randurile CompanyCaenCode deja existente -- nu insereaza
randuri noi, nici cand ANAF intoarce un cod pe care ONRC nu l-a listat printre codurile autorizate
ale firmei (acel caz e marcat separat, caen_principal_status="cod_lipsa", ca sa fie vizibil si
filtrabil, nu ascuns intr-un "not_found").

Reluabil: Company.caen_principal_status / caen_principal_verificat_la (acelasi model ca
geocode_status/geocoded_at in geocode_companies.py) tin evidenta a ce a fost deja verificat, ca
rularea sa poata fi intrerupta si reluata fara sa reinterogheze ANAF de la zero peste (potential)
milioane de firme. Foloseste rotatia de proxy-uri din scripts/proxies.py (ca ArcGisProvider in
services/geocoding.py), thread-safe desi aici request-urile sunt secventiale (un singur POST per
batch, nu unul per firma).
"""
import argparse
import itertools
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from sqlalchemy import select

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company, CompanyCaenCode
from scripts.proxies import get_requests_proxy, proxy_list


ANAF_URL = "https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva"
ANAF_MAX_BATCH = 500  # limita ANAF, nu doar o valoare implicita -- request-urile mai mari esueaza
DEFAULT_SLEEP = 0.6  # ANAF ~1 req/s


class _ProxyCycle:
    """Rotatie thread-safe peste scripts.proxies.proxy_list, ca in
    services/geocoding.py::ArcGisProvider -- pastrata aici local (nu extrasa intr-un
    modul comun) pentru ca, spre deosebire de geocodare, nu exista niciun endpoint API
    care sa aiba nevoie de acelasi client ANAF.
    """

    def __init__(self, use_proxy: bool):
        self._use_proxy = use_proxy and bool(proxy_list)
        self._cycle = itertools.cycle(proxy_list) if proxy_list else None
        self._lock = threading.Lock()

    def next(self) -> dict[str, str] | None:
        if not self._use_proxy or self._cycle is None:
            return None
        with self._lock:
            proxy_dict = next(self._cycle)
        return get_requests_proxy(proxy_dict)


def _normalize_caen_code(cod_caen) -> str | None:
    """company_caen_codes.caen_code e stocat cu 4 cifre, zero-padded (vezi
    od_caen_autorizat.csv: "0142", "6201"); ANAF poate intoarce cod_CAEN fara zero-ul
    de inceput pentru codurile din diviziunile 01-09.
    """
    if cod_caen is None:
        return None
    text_value = str(cod_caen).strip()
    if not text_value:
        return None
    return text_value.zfill(4)


def _query_anaf_batch(
    cuis: list[int], data: str, proxies: dict[str, str] | None, timeout: float = 30.0
) -> tuple[dict[int, str], set[int]]:
    """Un singur POST catre ANAF pentru pana la ANAF_MAX_BATCH CUI-uri. Returns
    (cui -> cod CAEN principal normalizat la 4 cifre, set de CUI negasite la ANAF).
    CUI-urile gasite dar fara cod_CAEN in raspuns nu apar in niciuna din cele doua --
    tratate ca eroare de date de la ANAF, nu ca "negasit".
    """
    body = [{"cui": cui, "data": data} for cui in cuis]
    response = requests.post(
        ANAF_URL,
        json=body,
        headers={"Content-Type": "application/json"},
        proxies=proxies,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()

    found: dict[int, str] = {}
    for entry in payload.get("found", []):
        date_generale = entry.get("date_generale") or {}
        cui = date_generale.get("cui")
        cod_caen = _normalize_caen_code(date_generale.get("cod_CAEN"))
        if cui is not None and cod_caen is not None:
            found[int(cui)] = cod_caen

    not_found = {int(cui) for cui in payload.get("notFound", [])}
    return found, not_found


@dataclass
class UpdateStats:
    processed: int = 0
    ok: int = 0
    cod_lipsa: int = 0
    not_found: int = 0
    error: int = 0

    def merge(self, other: "UpdateStats") -> None:
        self.processed += other.processed
        self.ok += other.ok
        self.cod_lipsa += other.cod_lipsa
        self.not_found += other.not_found
        self.error += other.error


def _fetch_batch(session, batch_size: int, last_id: int, retry_failed: bool) -> list[Company]:
    stmt = select(Company).where(Company.id > last_id)
    if retry_failed:
        stmt = stmt.where(Company.caen_principal_status.in_(["error", "not_found"]))
    else:
        stmt = stmt.where(Company.caen_principal_status.is_(None))
    stmt = stmt.order_by(Company.id).limit(batch_size)
    return list(session.scalars(stmt))


def _apply_principal(session, company: Company, cod_caen_principal: str) -> str:
    """Seteaza is_principal DOAR pe randul CompanyCaenCode deja existent care se
    potriveste cu codul intors de ANAF (niciun rand nou inserat -- cerinta explicita).
    Returns "ok" daca a gasit si actualizat un rand, "cod_lipsa" altfel.
    """
    rows = session.scalars(
        select(CompanyCaenCode).where(CompanyCaenCode.company_id == company.id)
    ).all()

    matched = next((row for row in rows if row.caen_code == cod_caen_principal), None)
    if matched is None:
        return "cod_lipsa"

    for row in rows:
        row.is_principal = row.id == matched.id
    return "ok"


def _process_batch(
    fetch_size: int,
    last_id: int,
    retry_failed: bool,
    dry_run: bool,
    proxy_cycle: _ProxyCycle,
    data: str,
) -> tuple[int, UpdateStats] | None:
    """Fetch, interoghează ANAF, si commite un singur batch. Returns (new_last_id,
    batch stats), sau None daca nu mai sunt randuri. La eroare de request, nicio firma
    din batch nu e marcata (raman caen_principal_status=NULL, reincercate automat la
    urmatoarea rulare fara --retry-failed).
    """
    with SessionLocal() as session:
        batch = _fetch_batch(session, fetch_size, last_id, retry_failed)
        if not batch:
            return None

        new_last_id = batch[-1].id
        batch_stats = UpdateStats(processed=len(batch))
        cuis = [company.cui for company in batch]
        companies_by_cui = {company.cui: company for company in batch}
        
        print(f"[pana la id {new_last_id}] procesez batch de {len(batch)} companii...")

        try:
            found, not_found_cuis = _query_anaf_batch(cuis, data, proxy_cycle.next())
        except requests.RequestException as exc:
            print(f"[pana la id {new_last_id}] eroare request ANAF ({len(batch)} CUI): {exc}")
            batch_stats.error = len(batch)
            return new_last_id, batch_stats

        if dry_run:
            for cui in cuis:
                if cui in found:
                    print(f"[{cui}] CAEN principal ANAF: {found[cui]}")
                else:
                    print(f"[{cui}] negasit la ANAF")
            return new_last_id, batch_stats

        now = datetime.now(timezone.utc)
        for cui in cuis:
            company = companies_by_cui[cui]
            if cui in found:
                status = _apply_principal(session, company, found[cui])
            else:
                status = "not_found"

            company.caen_principal_status = status
            company.caen_principal_verificat_la = now

            if status == "ok":
                batch_stats.ok += 1
            elif status == "cod_lipsa":
                batch_stats.cod_lipsa += 1
            else:
                batch_stats.not_found += 1

        session.commit()

    return new_last_id, batch_stats


def update_company_caen_principal(
    batch_size: int = ANAF_MAX_BATCH,
    limit: int | None = None,
    sleep: float = DEFAULT_SLEEP,
    retry_failed: bool = False,
    dry_run: bool = False,
    use_proxy: bool = True,
    data: str | None = None,
) -> UpdateStats:
    init_postgres()

    data = data or date.today().isoformat()
    batch_size = min(batch_size, ANAF_MAX_BATCH)
    proxy_cycle = _ProxyCycle(use_proxy)
    stats = UpdateStats()
    last_id = 0

    while limit is None or stats.processed < limit:
        remaining = None if limit is None else limit - stats.processed
        fetch_size = batch_size if remaining is None else min(batch_size, remaining)

        result = _process_batch(fetch_size, last_id, retry_failed, dry_run, proxy_cycle, data)
        if result is None:
            break

        last_id, batch_stats = result
        stats.merge(batch_stats)

        print(
            f"Progres: {stats.processed} procesate "
            f"({stats.ok} ok, {stats.cod_lipsa} cod lipsa, "
            f"{stats.not_found} negasite, {stats.error} erori)"
        )

        if sleep:
            time.sleep(sleep)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Actualizeaza CompanyCaenCode.is_principal din ANAF PlatitorTvaRest v9 (live)."
    )
    parser.add_argument(
        "--batch-size", type=int, default=ANAF_MAX_BATCH,
        help=f"CUI per request ANAF (limita ANAF: {ANAF_MAX_BATCH})",
    )
    parser.add_argument("--limit", type=int, default=None, help="Numarul maxim de firme de procesat in aceasta rulare")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP, help="Pauza (secunde) intre batch-uri catre ANAF")
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Reincearca si firmele cu status 'error' sau 'not_found', nu doar cele neverificate",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Afiseaza ce ar interoga/scrie, fara sa modifice baza de date",
    )
    parser.add_argument(
        "--no-proxy", action="store_true",
        help="Nu folosi proxy-urile din scripts/proxies.py, cere direct catre ANAF",
    )
    parser.add_argument("--data", default=None, help="Data (YYYY-MM-DD) pentru interogarea ANAF, implicit azi")
    args = parser.parse_args()

    stats = update_company_caen_principal(
        batch_size=args.batch_size,
        limit=args.limit,
        sleep=args.sleep,
        retry_failed=args.retry_failed,
        dry_run=args.dry_run,
        use_proxy=not args.no_proxy,
        data=args.data,
    )
    print("Actualizare finalizata.")
    print(f"Procesate: {stats.processed}")
    print(f"Ok: {stats.ok}")
    print(f"Cod lipsa (ANAF are un cod pe care ONRC nu-l lista pentru firma): {stats.cod_lipsa}")
    print(f"Negasite la ANAF: {stats.not_found}")
    print(f"Erori de request (reincercate automat la urmatoarea rulare): {stats.error}")


if __name__ == "__main__":
    main()
