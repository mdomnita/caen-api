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


def _iter_rows_with_principal_flag(file_path: Path):
    """Yields (registration_number, caen_code, caen_version, is_principal).

    The source file lists every row for a given COD_INMATRICULARE contiguously
    (as exported by ONRC), so the first row seen for a registration number is
    treated as the principal CAEN code and the rest as secondary.
    """
    previous_registration_number: str | None = None

    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="^", quoting=csv.QUOTE_NONE)
        for row in reader:
            registration_number = clean_text(row.get(SOURCE_COLUMNS["registration_number"]))
            caen_code = clean_text(row.get(SOURCE_COLUMNS["caen_code"]))
            caen_version = clean_text(row.get(SOURCE_COLUMNS["caen_version"]))

            if not registration_number or not caen_code:
                yield None, None, None, None
                continue

            is_principal = registration_number != previous_registration_number
            previous_registration_number = registration_number
            yield registration_number, caen_code, caen_version, is_principal


def _read_batches(file_path: Path, batch_size: int):
    batch: list[tuple[str, str, str | None, bool]] = []
    invalid_rows = 0

    for registration_number, caen_code, caen_version, is_principal in _iter_rows_with_principal_flag(file_path):
        if registration_number is None:
            invalid_rows += 1
            if invalid_rows and (len(batch) + invalid_rows) >= batch_size:
                yield batch, invalid_rows
                batch, invalid_rows = [], 0
            continue

        batch.append((registration_number, caen_code, caen_version, is_principal))
        if len(batch) >= batch_size:
            yield batch, invalid_rows
            batch, invalid_rows = [], 0

    if batch or invalid_rows:
        yield batch, invalid_rows


def _upsert_batch(batch: list[tuple[str, str, str | None, bool]], invalid_rows: int) -> ImportStats:
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

        payload = []
        for registration_number, caen_code, caen_version, is_principal in batch:
            company_id = company_ids.get(registration_number)
            if company_id is None:
                stats.skipped_no_company += 1
                continue
            payload.append(
                {
                    "company_id": company_id,
                    "caen_code": caen_code,
                    "is_principal": is_principal,
                    "caen_version": caen_version,
                }
            )

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
    parser = argparse.ArgumentParser(description="Import coduri CAEN autorizate (principal/secundar) in PostgreSQL")
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
