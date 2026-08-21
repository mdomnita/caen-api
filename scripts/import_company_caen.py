import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company, CompanyCaenCode
from routers.company_utils import clean_text


SOURCE_COLUMNS = {
    "registration_number": "COD_INMATRICULARE",
    "caen_code": "COD_CAEN_AUTORIZAT",
    "caen_version": "VER_CAEN_AUTORIZAT",
}

# VER_CAEN_AUTORIZAT is a human-readable "Versiunea NNNN" string in older ONRC exports
# but a bare nomenclature code (matching n_versiune_caen.csv) in newer ones; normalize
# either shape to the text form so caen_version is consistent regardless of source file.
_VERSION_CODE_LABELS = {
    "0": "Versiunea 1998",
    "1": "Versiunea 2003",
    "2": "Versiunea 2008",
    "3": "Versiunea 2025",  # CAEN Rev 3
}


def _normalize_caen_version(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None
    return _VERSION_CODE_LABELS.get(raw_value, raw_value)


@dataclass
class ImportStats:
    rows_seen: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_no_company: int = 0
    errors: int = 0

    @property
    def processed(self) -> int:
        return self.inserted + self.updated

    def merge(self, other: "ImportStats") -> None:
        self.rows_seen += other.rows_seen
        self.inserted += other.inserted
        self.updated += other.updated
        self.skipped_no_company += other.skipped_no_company
        self.errors += other.errors


def _iter_rows(file_path: Path):
    """Yields (registration_number, caen_code, caen_version).

    od_caen_autorizat.csv lists every authorized activity code for a company, sorted
    by CAEN code -- NOT principal-first. ONRC's bulk exports don't mark which code is
    the registered principal activity anywhere, so this importer treats every row as
    an authorized code of unknown principal/secondary status (CompanyCaenCode.is_principal
    is always False here). A prior version of this script wrongly assumed the first row
    per registration number was principal; it wasn't -- it was just the numerically
    smallest code.
    """
    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="^", quoting=csv.QUOTE_NONE)
        for row in reader:
            registration_number = clean_text(row.get(SOURCE_COLUMNS["registration_number"]))
            caen_code = clean_text(row.get(SOURCE_COLUMNS["caen_code"]))
            caen_version = _normalize_caen_version(clean_text(row.get(SOURCE_COLUMNS["caen_version"])))

            if not registration_number or not caen_code:
                yield None, None, None
                continue

            yield registration_number, caen_code, caen_version


def _read_batches(file_path: Path, batch_size: int):
    batch: list[tuple[str, str, str | None]] = []
    invalid_rows = 0

    for registration_number, caen_code, caen_version in _iter_rows(file_path):
        if registration_number is None:
            invalid_rows += 1
            if invalid_rows and (len(batch) + invalid_rows) >= batch_size:
                yield batch, invalid_rows
                batch, invalid_rows = [], 0
            continue

        batch.append((registration_number, caen_code, caen_version))
        if len(batch) >= batch_size:
            yield batch, invalid_rows
            batch, invalid_rows = [], 0

    if batch or invalid_rows:
        yield batch, invalid_rows


def _upsert_batch(batch: list[tuple[str, str, str | None]], invalid_rows: int) -> ImportStats:
    stats = ImportStats(rows_seen=len(batch) + invalid_rows, errors=invalid_rows)
    if not batch:
        return stats

    with SessionLocal() as session:
        registration_numbers = {row[0] for row in batch}
        company_ids = dict(
            session.execute(
                select(Company.registration_number, Company.id).where(
                    Company.registration_number.in_(registration_numbers)
                )
            ).all()
        )

        payload_by_key: dict[tuple[int, str], dict] = {}
        for registration_number, caen_code, caen_version in batch:
            company_id = company_ids.get(registration_number)
            if company_id is None:
                stats.skipped_no_company += 1
                continue
            key = (company_id, caen_code)
            if key in payload_by_key:
                # A registration number's rows aren't always contiguous in the source
                # file, so the same (company, CAEN code) pair can appear more than once
                # in a batch; ON CONFLICT DO UPDATE cannot touch the same row twice
                # within one statement, so keep a single entry per key.
                continue
            payload_by_key[key] = {
                "company_id": company_id,
                "caen_code": caen_code,
                "is_principal": False,  # not derivable from this source -- see _iter_rows
                "caen_version": caen_version,
            }
        payload = list(payload_by_key.values())

        if not payload:
            return stats

        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            stmt = pg_insert(CompanyCaenCode).values(payload)
            stmt = stmt.on_conflict_do_update(
                index_elements=[CompanyCaenCode.company_id, CompanyCaenCode.caen_code],
                set_={
                    "is_principal": stmt.excluded.is_principal,
                    "caen_version": stmt.excluded.caen_version,
                },
            )
            result = session.execute(stmt)
            stats.inserted += len(payload)
        else:
            for row in payload:
                existing = (
                    session.query(CompanyCaenCode)
                    .filter(
                        CompanyCaenCode.company_id == row["company_id"],
                        CompanyCaenCode.caen_code == row["caen_code"],
                    )
                    .one_or_none()
                )
                if existing is None:
                    session.add(CompanyCaenCode(**row))
                    stats.inserted += 1
                else:
                    existing.is_principal = row["is_principal"]
                    existing.caen_version = row["caen_version"]
                    stats.updated += 1
        session.commit()
    return stats


def import_company_caen(file_path: Path, batch_size: int = 5000, truncate: bool = False) -> ImportStats:
    init_postgres()

    if truncate:
        with SessionLocal() as session:
            session.execute(delete(CompanyCaenCode))
            session.commit()

    total = ImportStats()
    for batch, invalid_rows in _read_batches(file_path, batch_size=batch_size):
        total.merge(_upsert_batch(batch, invalid_rows))
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import coduri CAEN autorizate in PostgreSQL (principalul nu e marcat in sursa ONRC)"
    )
    parser.add_argument("--file", required=True, help="Calea catre fisierul od_caen_autorizat.csv")
    parser.add_argument("--batch-size", type=int, default=5000, help="Numarul de randuri per batch")
    parser.add_argument("--truncate", action="store_true", help="Sterge tabela inainte de import")
    args = parser.parse_args()

    stats = import_company_caen(Path(args.file), batch_size=args.batch_size, truncate=args.truncate)
    print(f"Import finalizat. Randuri citite: {stats.rows_seen}")
    print(f"Inserate: {stats.inserted}")
    print(f"Actualizate: {stats.updated}")
    print(f"Ignorate (companie negasita dupa nr. inmatriculare): {stats.skipped_no_company}")
    print(f"Erori / randuri invalide: {stats.errors}")


if __name__ == "__main__":
    main()
