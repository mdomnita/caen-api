"""Deriva o fereastra aproximativa de inchidere per firma, din instantanee ONRC istorice.

Nicio sursa deschisa (od_stare_firma.csv, od_firme.csv) nu contine o data exacta de radiere --
doar codul de stare curent. Dar temp/onrc/ contine ~35 instantanee istorice datate (2015-07-31 ->
2025-03-18), fiecare cu 4 fisiere care impreuna acopera toate firmele la acel moment:

    1*neradiate_fara_sediu*.csv, 2*radiate_fara_sediu*.csv   -- firme fara sediu cunoscut (mici)
    3*neradiate_cu_sediu*.csv,   4*radiate_cu_sediu*.csv     -- marea majoritate (~2M randuri/fisier)

Eticheta "neradiate"/"radiate" din numele fisierului NU e de incredere singura -- coloana
STARE_FIRMA din interior poate contine orice cod (verificat empiric: un rand dintr-un fisier
"neradiate" avea STARE_FIRMA=1052 "lichidare"), uneori mai multe coduri separate prin virgula in
aceeasi celula. Starea reala la acel moment se recalculeaza din STARE_FIRMA cu exact aceeasi
logica firma_activa() din update_company_stare.py, indiferent din care din cele 4 fisiere provine
randul.

Rezultat: pentru fiecare firma deja marcata is_active=False (de update_company_stare.py), se
calculeaza ultima_data_activa_cunoscuta (cea mai recenta instantanee in care a fost vazuta activa)
si prima_data_inactiva_cunoscuta (prima instantanee DUPA aceea in care a fost vazuta inactiva) --
fereastra reala de inchidere e undeva intre cele doua. Daca firma era deja inactiva in cea mai
veche instantanee disponibila (2015-07-31), fereastra nu poate fi incadrata -- doar
prima_data_inactiva_cunoscuta e utila, ca limita superioara (fereastra_inchidere_tip=
"necunoscuta_inainte_de_2015").

ATENTIE la scara: ~35 instantanee x 4 fisiere x pana la ~2M randuri = zeci de milioane de randuri
in total. Masurat: ~19s pentru cele 4 fisiere ale unei singure instantanee recente (~4M randuri) --
o rulare completa peste toate instantaneele dureaza aproximativ 10-20 minute (partea de citire
CSV; scrierea in DB e separata, per batch). Foloseste --since/--until pentru testare rapida pe un
subset inainte de o rulare completa.
"""
import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company
from scripts.update_company_stare import firma_activa


DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "temp" / "onrc"

_LUNI_RO = {
    "ianuarie": 1, "februarie": 2, "martie": 3, "aprilie": 4, "mai": 5, "iunie": 6,
    "iulie": 7, "august": 8, "septembrie": 9, "octombrie": 10, "noiembrie": 11, "decembrie": 12,
}

_DATE_DOT = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")            # DD.MM.YYYY
_DATE_DASH_DMY = re.compile(r"(\d{2})-(\d{2})-(\d{4})")          # DD-MM-YYYY
_DATE_DASH_YMD = re.compile(r"(\d{4})-(\d{2})-(\d{2})")          # YYYY-MM-DD
_DATE_RO_MONTH = re.compile(
    r"(\d{1,2})[\s\-_]*(" + "|".join(_LUNI_RO) + r")[\s\-_]*(\d{4})", re.IGNORECASE
)


def _date_from_text(text: str) -> date | None:
    m = _DATE_DOT.search(text)
    if m:
        dd, mm, yyyy = (int(g) for g in m.groups())
        return date(yyyy, mm, dd)
    m = _DATE_DASH_DMY.search(text)
    if m:
        dd, mm, yyyy = (int(g) for g in m.groups())
        return date(yyyy, mm, dd)
    m = _DATE_DASH_YMD.search(text)
    if m:
        yyyy, mm, dd = (int(g) for g in m.groups())
        return date(yyyy, mm, dd)
    m = _DATE_RO_MONTH.search(text)
    if m:
        dd, luna, yyyy = m.groups()
        return date(int(yyyy), _LUNI_RO[luna.lower()], int(dd))
    return None


def snapshot_date(path: Path) -> date | None:
    """Data instantaneei: aproape mereu in numele fisierului; fallback pe numele
    folderului parinte (nume de luna in romana) pentru fisierele fara sufix de data.
    """
    return _date_from_text(path.stem) or _date_from_text(path.parent.name)


def _is_snapshot_file(path: Path) -> bool:
    name = path.name.lower()
    if not name.endswith(".csv"):
        return False
    if name[0] not in "1234":
        return False
    return "radiate" in name and "sediu" in name


def discover_snapshots(root: Path, since: date | None = None, until: date | None = None) -> list[tuple[date, Path]]:
    """Toate fisierele-instantanee gasite sub root, cu data lor, filtrate optional pe
    interval si sortate crescator dupa data. Fisierele fara data detectabila sunt sarite
    (loghează un avertisment -- niciun fisier confirmat din temp/onrc/ nu cade in acest caz).
    """
    found: list[tuple[date, Path]] = []
    for path in root.rglob("*.csv"):
        if not _is_snapshot_file(path):
            continue
        snap_date = snapshot_date(path)
        if snap_date is None:
            print(f"AVERTISMENT: nu pot determina data instantaneei pentru {path} -- sarit")
            continue
        if since is not None and snap_date < since:
            continue
        if until is not None and snap_date > until:
            continue
        found.append((snap_date, path))
    found.sort(key=lambda item: item[0])
    return found


def _iter_cui_active(path: Path):
    """Yields (cui, is_active) pentru fiecare rand valid dintr-un fisier-instantanee.

    Unele instantanee mai vechi nu sunt UTF-8 valid (probabil cp1250/Windows-1252 --
    exporturi ONRC mai vechi). Singurele coloane citite (CUI, STARE_FIRMA) sunt ASCII
    pur, deci un octet nedecodabil in alta coloana (ex. diacritice in DENUMIRE) nu
    trebuie sa opreasca tot fisierul -- errors="replace" il inlocuieste cu U+FFFD si
    continua.
    """
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        first_line = handle.readline()
        delimiter = "^" if "^" in first_line else "|"
        handle.seek(0)
        reader = csv.reader(handle, delimiter=delimiter, quoting=csv.QUOTE_NONE)

        header = [cell.strip() for cell in next(reader)]
        try:
            cui_idx = header.index("CUI")
            stare_idx = header.index("STARE_FIRMA")
        except ValueError:
            print(f"AVERTISMENT: {path} nu are coloanele CUI/STARE_FIRMA asteptate -- sarit")
            return

        needed = max(cui_idx, stare_idx) + 1
        for row in reader:
            if len(row) < needed:
                continue
            cui_raw = row[cui_idx].strip()
            if not cui_raw.isdigit() or cui_raw == "0":
                continue
            codes = [c.strip() for c in row[stare_idx].split(",") if c.strip()]
            yield int(cui_raw), firma_activa(codes)


@dataclass(slots=True)
class _CuiState:
    """Stare compacta per CUI -- doar 3 date, nu tot istoricul de coduri STARE_FIRMA
    vazute, ca sa tina consumul de memorie mic la milioane de firme unice (__slots__
    evita overhead-ul de __dict__ per instanta, semnificativ la aceasta scara)."""

    last_active: date | None = None
    first_inactive_after: date | None = None
    earliest_inactive_no_active: date | None = None


@dataclass
class ClosureResult:
    ultima_data_activa: date | None
    prima_data_inactiva: date | None
    tip: str | None


def _finalize(state: _CuiState) -> ClosureResult | None:
    if state.last_active is not None and state.first_inactive_after is not None:
        return ClosureResult(state.last_active, state.first_inactive_after, "incadrata")
    if state.last_active is None and state.earliest_inactive_no_active is not None:
        return ClosureResult(None, state.earliest_inactive_no_active, "necunoscuta_inainte_de_2015")
    return None  # niciodata vazuta inactiva (sau doar activa) -- nimic de scris


def build_closure_windows(snapshots: list[tuple[date, Path]]) -> dict[int, ClosureResult]:
    """Un singur fold secvential, in ordine cronologica, peste toate instantaneele.
    Stare compacta per CUI (3 date, nu tot istoricul de coduri) -- fezabil in memorie
    chiar la milioane de firme unice.
    """
    states: dict[int, _CuiState] = {}

    for snap_date, path in snapshots:
        print(f"[{snap_date}] {path}")
        for cui, is_active in _iter_cui_active(path):
            state = states.get(cui)
            if state is None:
                state = _CuiState()
                states[cui] = state

            if is_active:
                state.last_active = snap_date
                state.first_inactive_after = None
            else:
                if state.last_active is not None and state.first_inactive_after is None:
                    state.first_inactive_after = snap_date
                elif state.last_active is None and state.earliest_inactive_no_active is None:
                    state.earliest_inactive_no_active = snap_date

    results: dict[int, ClosureResult] = {}
    for cui, state in states.items():
        result = _finalize(state)
        if result is not None:
            results[cui] = result
    return results


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


@dataclass
class UpdateStats:
    candidates: int = 0
    updated: int = 0
    skipped_not_inactive_or_missing: int = 0


def update_closure_windows(
    results: dict[int, ClosureResult], batch_size: int = 5000, dry_run: bool = False
) -> UpdateStats:
    stats = UpdateStats(candidates=len(results))
    cuis = list(results.keys())

    for batch in _chunks(cuis, batch_size):
        with SessionLocal() as session:
            companies = session.scalars(
                select(Company).where(Company.cui.in_(batch), Company.is_active.is_(False))
            ).all()

            for company in companies:
                result = results[company.cui]
                if dry_run:
                    print(
                        f"[{company.cui}] ultima_activa={result.ultima_data_activa} "
                        f"prima_inactiva={result.prima_data_inactiva} tip={result.tip}"
                    )
                else:
                    company.ultima_data_activa_cunoscuta = result.ultima_data_activa
                    company.prima_data_inactiva_cunoscuta = result.prima_data_inactiva
                    company.fereastra_inchidere_tip = result.tip
                stats.updated += 1

            stats.skipped_not_inactive_or_missing += len(batch) - len(companies)

            if not dry_run:
                session.commit()

    return stats


def derive_company_closure_window(
    root: Path = DEFAULT_ROOT,
    since: date | None = None,
    until: date | None = None,
    batch_size: int = 5000,
    dry_run: bool = False,
) -> tuple[int, UpdateStats]:
    init_postgres()

    snapshots = discover_snapshots(root, since=since, until=until)
    print(f"Instantanee gasite: {len(snapshots)}")
    if not snapshots:
        return 0, UpdateStats()

    results = build_closure_windows(snapshots)
    stats = update_closure_windows(results, batch_size=batch_size, dry_run=dry_run)
    return len(snapshots), stats


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deriva o fereastra aproximativa de inchidere per firma din instantanee ONRC istorice."
    )
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="Folderul cu instantanee (implicit temp/onrc)")
    parser.add_argument("--since", type=_parse_date, default=None, help="Proceseaza doar instantanee de la aceasta data (YYYY-MM-DD)")
    parser.add_argument("--until", type=_parse_date, default=None, help="Proceseaza doar instantanee pana la aceasta data (YYYY-MM-DD)")
    parser.add_argument("--batch-size", type=int, default=5000, help="Numarul de firme per batch de scriere")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Afiseaza ferestrele calculate, fara a modifica baza de date",
    )
    args = parser.parse_args()

    snapshot_count, stats = derive_company_closure_window(
        root=Path(args.root),
        since=args.since,
        until=args.until,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )
    print(f"Instantanee procesate: {snapshot_count}")
    print(f"Firme cu fereastra calculata: {stats.candidates}")
    print(f"Actualizate (firme is_active=False gasite): {stats.updated}")
    print(f"Ignorate (nu erau is_active=False sau nu au fost gasite dupa CUI): {stats.skipped_not_inactive_or_missing}")


if __name__ == "__main__":
    main()
