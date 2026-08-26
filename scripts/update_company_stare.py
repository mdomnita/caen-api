"""Deriva Company.is_active din OD_STARE_FIRMA.CSV (nomenclator ONRC).

Sursa nu e importata nicaieri altundeva in pipeline (vezi DATABASE_SETUP.md §2.0): un rand per
(firma, cod stare) -- o firma poate avea mai multe randuri de stare de-a lungul timpului, iar
decizia daca firma opereaza azi se ia pe TOT SETUL de coduri al firmei, nu pe ultimul rand din
fisier (fisierul nu e garantat sortat/grupat pe COD_INMATRICULARE).

Nu insereaza/sterge randuri in `companies` -- doar actualizeaza is_active + stare_verificata_la
pentru firme deja importate (prin registration_number, ca in import_company_caen.py).
"""
import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
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


def update_company_stare(file_path: Path, batch_size: int = 5000, dry_run: bool = False) -> UpdateStats:
    init_postgres()

    status_by_registration, invalid_rows = _build_status_by_registration(file_path)
    stats = UpdateStats(rows_seen=len(status_by_registration), invalid_rows=invalid_rows)

    now = datetime.now(timezone.utc)
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

                if is_active:
                    stats.companies_active += 1
                else:
                    stats.companies_inactive += 1

            if not dry_run:
                session.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Actualizeaza Company.is_active din OD_STARE_FIRMA.CSV (nomenclator ONRC)."
    )
    parser.add_argument("--file", required=True, help="Calea catre fisierul od_stare_firma.csv")
    parser.add_argument("--batch-size", type=int, default=5000, help="Numarul de firme per batch de scriere")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Afiseaza deciziile is_active care ar fi scrise, fara a modifica baza de date",
    )
    args = parser.parse_args()

    stats = update_company_stare(Path(args.file), batch_size=args.batch_size, dry_run=args.dry_run)
    print(f"Firme unice in sursa: {stats.rows_seen}")
    print(f"Actualizate: {stats.updated} ({stats.companies_active} active, {stats.companies_inactive} inactive)")
    print(f"Ignorate (companie negasita dupa nr. inmatriculare): {stats.skipped_no_company}")
    print(f"Randuri invalide in sursa: {stats.invalid_rows}")


if __name__ == "__main__":
    main()
