"""Actualizeaza starea curenta si istoricul observabil al companiilor din exporturile ONRC.

Sursa nu e importata nicaieri altundeva in pipeline (vezi DATABASE_SETUP.md §2.0): un rand per
(firma, cod stare) -- o firma poate avea mai multe randuri de stare de-a lungul timpului, iar
decizia daca firma opereaza azi se ia pe TOT SETUL de coduri al firmei, nu pe ultimul rand din
fisier (fisierul nu e garantat sortat/grupat pe COD_INMATRICULARE).

Implicit, dupa starea curenta, parcurge si instantaneele istorice din temp/onrc. Datele istorice
reprezinta momente de observare in snapshot-uri, nu date juridice exacte ale evenimentelor:

* ultima_data_activa_cunoscuta -- ultimul snapshot in care firma apare activa;
* prima_data_inactiva_cunoscuta -- primul snapshot ulterior in care apare inactiva;
* prima_data_radiata_cunoscuta -- primul snapshot in care apare codul 1084 (radiata);
* fereastra_inchidere_tip -- arata daca tranzitia este incadrata intre doua snapshot-uri.

Nu insereaza si nu sterge firme. Actualizeaza numai companii deja importate, prin numarul de
inmatriculare pentru starea curenta si prin CUI pentru istoricul snapshot-urilor.
"""
import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company
from routers.company_utils import clean_text


SOURCE_COLUMNS = {
    "registration_number": "COD_INMATRICULARE",
    "cod": "COD",
}

# ACTIVE_STARE = singurul cod care spune fara dubiu ca firma opereaza.
ACTIVE_STARE = "1048"

# NOT_OPERATING_STARE = coduri care ANULEAZA 1048: radiere / lichidare / faliment /
# insolventa / suspendare. Capcane: 1139 = insolventa (nu "inmatriculare"); 1052 =
# lichidare (nu "dizolvare"). NU pune aici (firma inca tranzactioneaza): 1036/1067/1069
# (penal pe firma), 1037/1038/1146 (firma-mama e insolventa), 1050/1054 (fuziune/divizare),
# 1053 (schimbare sediu), 1110. Legenda completa = nomenclatorul ONRC N_STARE_FIRMA.CSV.
# Nu ghici etichetele.
NOT_OPERATING_STARE = {
    "1049", "1052", "1055", "1057", "1070", "1073", "1074", "1076", "1078", "1083",
    "1084", "1086", "1094", "1098", "1100", "1105", "1106", "1107", "1109", "1111",
    "1113", "1120", "1121", "1126", "1132", "1133", "1134", "1137", "1138", "1139",
    "1144", "1145", "1151", "1152", "1153",
}

_HAS_ACTIVE = 1
_HAS_NOT_OPERATING = 2
_HAS_RADIATED = 4


def source_snapshot_date(file_path: Path) -> date | None:
    """Extrage data snapshot-ului din numele fisierului sau al folderului parinte."""
    for text in (file_path.stem, file_path.parent.name):
        match = re.search(r"(?<!\d)(\d{2})[-.](\d{2})[-.](\d{4})(?!\d)", text)
        if match:
            day, month, year = (int(value) for value in match.groups())
            return date(year, month, day)
        match = re.search(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", text)
        if match:
            year, month, day = (int(value) for value in match.groups())
            return date(year, month, day)
    return None


def discover_status_sources(path: Path) -> list[tuple[date | None, Path]]:
    """Returneaza sursele moderne ``od_stare_firma.csv`` in ordine cronologica.

    Pentru o cale catre fisier se pastreaza comportamentul vechi. Pentru un director se cauta
    recursiv numai fisierele od_stare_firma.csv; fisierele istorice 1/2/3/4 sunt procesate separat
    de derive_company_closure_window.py, deoarece au alta schema.
    """
    if path.is_file():
        return [(source_snapshot_date(path), path)]
    if not path.is_dir():
        raise FileNotFoundError(f"Sursa ONRC nu exista: {path}")

    sources = [
        (source_snapshot_date(candidate), candidate)
        for candidate in path.rglob("od_stare_firma.csv")
        if candidate.is_file()
    ]
    sources.sort(key=lambda item: (item[0] is None, item[0] or date.max, str(item[1])))
    return sources


def firma_activa(coduri_stare) -> bool:
    """True daca setul complet de coduri al firmei spune ca opereaza azi."""
    coduri = {str(c).strip() for c in coduri_stare}
    if coduri & NOT_OPERATING_STARE:
        return False
    return ACTIVE_STARE in coduri


def _iter_rows(file_path: Path):
    """Yields (registration_number, cod) -- (None, None) pentru randuri invalide."""
    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="^", quoting=csv.QUOTE_NONE)
        for row in reader:
            registration_number = clean_text(row.get(SOURCE_COLUMNS["registration_number"]))
            cod = clean_text(row.get(SOURCE_COLUMNS["cod"]))
            if not registration_number or not cod:
                yield None, None
                continue
            yield registration_number, cod


def _build_status_by_registration(file_path: Path) -> tuple[dict[str, int], int]:
    """Un singur pass peste fisier: pentru fiecare registration_number, retine doar 2
    biti (are codul ACTIVE_STARE? are vreun cod din NOT_OPERATING_STARE?) -- nu tot
    setul de coduri brute, ca sa tina consumul de memorie mic chiar la milioane de
    randuri/firme unice.
    """
    status_by_registration: dict[str, int] = {}
    invalid_rows = 0

    for registration_number, cod in _iter_rows(file_path):
        if registration_number is None:
            invalid_rows += 1
            continue

        mask = status_by_registration.get(registration_number, 0)
        if cod == ACTIVE_STARE:
            mask |= _HAS_ACTIVE
        elif cod in NOT_OPERATING_STARE:
            mask |= _HAS_NOT_OPERATING
        if cod == "1084":
            mask |= _HAS_RADIATED
        status_by_registration[registration_number] = mask

    return status_by_registration, invalid_rows


@dataclass
class UpdateStats:
    rows_seen: int = 0
    companies_active: int = 0
    companies_inactive: int = 0
    skipped_no_company: int = 0
    invalid_rows: int = 0

    @property
    def updated(self) -> int:
        return self.companies_active + self.companies_inactive


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def update_company_stare(
    file_path: Path,
    batch_size: int = 5000,
    dry_run: bool = False,
    observed_at: date | None = None,
) -> UpdateStats:
    init_postgres()

    status_by_registration, invalid_rows = _build_status_by_registration(file_path)
    stats = UpdateStats(rows_seen=len(status_by_registration), invalid_rows=invalid_rows)

    now = datetime.now(timezone.utc)
    observed_at = observed_at or source_snapshot_date(file_path)
    registration_numbers = list(status_by_registration.keys())
    total_registrations = len(registration_numbers)
    processed = 0

    for batch in _chunks(registration_numbers, batch_size):
        processed += len(batch)
        print(f"Procesate: {processed}/{total_registrations}")
        with SessionLocal() as session:
            company_by_registration = {
                registration_number: company
                for registration_number, company in session.execute(
                    select(Company.registration_number, Company).where(
                        Company.registration_number.in_(batch)
                    )
                ).all()
            }

            for registration_number in batch:
                company = company_by_registration.get(registration_number)
                if company is None:
                    stats.skipped_no_company += 1
                    continue

                mask = status_by_registration[registration_number]
                is_active = bool(mask & _HAS_ACTIVE) and not bool(mask & _HAS_NOT_OPERATING)

                if dry_run:
                    print(f"[{company.cui}] is_active={is_active}")
                else:
                    company.is_active = is_active
                    company.stare_verificata_la = now
                    if not is_active and observed_at is not None:
                        if (
                            company.prima_data_inactiva_cunoscuta is None
                            or observed_at < company.prima_data_inactiva_cunoscuta
                        ):
                            company.prima_data_inactiva_cunoscuta = observed_at
                        if (mask & _HAS_RADIATED) and (
                            company.prima_data_radiata_cunoscuta is None
                            or observed_at < company.prima_data_radiata_cunoscuta
                        ):
                            company.prima_data_radiata_cunoscuta = observed_at

                if is_active:
                    stats.companies_active += 1
                else:
                    stats.companies_inactive += 1

            if not dry_run:
                session.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Actualizeaza starea curenta si datele la care schimbarile au fost observate "
            "in snapshot-urile ONRC. Datele observate nu sunt date juridice exacte."
        )
    )
    parser.add_argument(
        "--file",
        required=True,
        help=(
            "Fisier od_stare_firma.csv sau folder cautat recursiv; pentru toata arhiva "
            "foloseste temp/onrc"
        ),
    )
    parser.add_argument(
        "--observed-at",
        type=lambda value: datetime.strptime(value, "%Y-%m-%d").date(),
        default=None,
        help="Data snapshot-ului curent (YYYY-MM-DD); implicit este dedusa din cale",
    )
    parser.add_argument(
        "--history-root",
        default=str(Path(__file__).resolve().parents[1] / "temp" / "onrc"),
        help="Folderul cu snapshot-uri ONRC istorice (implicit temp/onrc)",
    )
    parser.add_argument(
        "--skip-history",
        action="store_true",
        help="Actualizeaza numai starea curenta, fara scanarea snapshot-urilor istorice",
    )
    parser.add_argument("--batch-size", type=int, default=5000, help="Numarul de firme per batch de scriere")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Afiseaza deciziile is_active care ar fi scrise, fara a modifica baza de date",
    )
    args = parser.parse_args()

    source_path = Path(args.file)
    sources = discover_status_sources(source_path)
    if not sources:
        parser.error(f"Nu am gasit niciun od_stare_firma.csv sub {source_path}")
    if args.observed_at is not None and len(sources) > 1:
        parser.error("--observed-at poate fi folosit numai cand --file indica un singur fisier")

    totals = UpdateStats()
    print(f"Surse moderne de stare gasite: {len(sources)}")
    for detected_date, file_path in sources:
        print(f"[{detected_date or 'data necunoscuta'}] {file_path}")
        stats = update_company_stare(
            file_path,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            observed_at=args.observed_at or detected_date,
        )
        totals.rows_seen += stats.rows_seen
        totals.companies_active += stats.companies_active
        totals.companies_inactive += stats.companies_inactive
        totals.skipped_no_company += stats.skipped_no_company
        totals.invalid_rows += stats.invalid_rows

    print(f"Inregistrari unice procesate cumulat: {totals.rows_seen}")
    print(
        f"Actualizari cumulate: {totals.updated} "
        f"({totals.companies_active} active, {totals.companies_inactive} inactive)"
    )
    print(f"Ignorate cumulat (companie negasita dupa nr. inmatriculare): {totals.skipped_no_company}")
    print(f"Randuri invalide cumulat: {totals.invalid_rows}")

    if not args.skip_history:
        # Import local pentru a evita o dependenta circulara la incarcarea modulelor:
        # derive_company_closure_window reutilizeaza firma_activa() din acest fisier.
        from scripts.derive_company_closure_window import derive_company_closure_window

        snapshot_count, history_stats = derive_company_closure_window(
            root=Path(args.history_root), batch_size=args.batch_size, dry_run=args.dry_run
        )
        print(f"Instantanee istorice procesate: {snapshot_count}")
        print(f"Firme cu schimbare observata: {history_stats.candidates}")
        print(f"Firme inactive cu istoric actualizat: {history_stats.updated}")


if __name__ == "__main__":
    main()
