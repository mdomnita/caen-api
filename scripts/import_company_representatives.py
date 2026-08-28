"""Import legal representatives (administrators, lichidatori, etc.) into company_representatives.

Source: temp/onrc/firme-<snapshot>/od_reprezentanti_legali.csv -- one row per (company,
representative), no CNP. A company can have several representatives (administrators, and
sometimes a "lichidator" during liquidation); the same person can also appear at several
companies. `CALITATE` carries the role (e.g. "administrator", "lichidator").

Matched by COD_INMATRICULARE == companies.registration_number, exactly like
scripts/import_company_caen.py -- NOT by CUI (this file doesn't have one).
"""
import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company, CompanyRepresentative
from routers.company_utils import clean_text, parse_ro_date


SOURCE_COLUMNS = {
    "registration_number": "COD_INMATRICULARE",
    "nume": "PERSOANA_IMPUTERNICITA",
    "calitate": "CALITATE",
    "data_nasterii": "DATA_NASTERE",
    "localitate_nasterii": "LOCALITATE_NASTERE",
    "judet_nasterii": "JUDET_NASTERE",
    "tara_nasterii": "TARA_NASTERE",
    "localitate": "LOCALITATE",
    "judet": "JUDET",
    "tara": "TARA",
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


def _iter_rows(file_path: Path):
    """Yields payload dicts (fara registration_number) -- None pentru randuri invalide."""
    with file_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="^")
        for row in reader:
            registration_number = clean_text(row.get(SOURCE_COLUMNS["registration_number"]))
            nume = clean_text(row.get(SOURCE_COLUMNS["nume"]))
            if not registration_number or not nume:
                yield None, None
                continue

            payload = {
                "nume": nume,
                "calitate": clean_text(row.get(SOURCE_COLUMNS["calitate"])),
                "data_nasterii": parse_ro_date(row.get(SOURCE_COLUMNS["data_nasterii"])),
                "localitate_nasterii": clean_text(row.get(SOURCE_COLUMNS["localitate_nasterii"])),
                "judet_nasterii": clean_text(row.get(SOURCE_COLUMNS["judet_nasterii"])),
                "tara_nasterii": clean_text(row.get(SOURCE_COLUMNS["tara_nasterii"])),
                "localitate": clean_text(row.get(SOURCE_COLUMNS["localitate"])),
                "judet": clean_text(row.get(SOURCE_COLUMNS["judet"])),
                "tara": clean_text(row.get(SOURCE_COLUMNS["tara"])),
            }
            yield registration_number, payload


def _read_batches(file_path: Path, batch_size: int):
    batch: list[tuple[str, dict]] = []
    invalid_rows = 0

    for registration_number, payload in _iter_rows(file_path):
        if registration_number is None:
            invalid_rows += 1
            if invalid_rows and (len(batch) + invalid_rows) >= batch_size:
                yield batch, invalid_rows
                batch, invalid_rows = [], 0
            continue

        batch.append((registration_number, payload))
        if len(batch) >= batch_size:
            yield batch, invalid_rows
            batch, invalid_rows = [], 0

    if batch or invalid_rows:
        yield batch, invalid_rows


def _upsert_batch(batch: list[tuple[str, dict]], invalid_rows: int) -> ImportStats:
    stats = ImportStats(rows_seen=len(batch) + invalid_rows, errors=invalid_rows)
    if not batch:
        return stats

    with SessionLocal() as session:
        registration_numbers = {registration_number for registration_number, _ in batch}
        company_ids = dict(
            session.execute(
                select(Company.registration_number, Company.id).where(
                    Company.registration_number.in_(registration_numbers)
                )
            ).all()
        )

        payload_by_key: dict[tuple[int, str, str | None], dict] = {}
        for registration_number, payload in batch:
            company_id = company_ids.get(registration_number)
            if company_id is None:
                stats.skipped_no_company += 1
                continue
            key = (company_id, payload["nume"], payload["calitate"])
            if key in payload_by_key:
                # Aceeasi persoana/rol poate apare de 2 ori pentru aceeasi firma daca randurile
                # nu sunt contigue in fisierul sursa -- ON CONFLICT DO UPDATE nu poate atinge
                # acelasi rand de doua ori intr-un singur statement.
                continue
            payload_by_key[key] = {"company_id": company_id, **payload}
        payload = list(payload_by_key.values())

        if not payload:
            return stats

        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            stmt = pg_insert(CompanyRepresentative).values(payload)
            update_cols = {
                k: stmt.excluded[k] for k in payload[0] if k not in ("company_id", "nume", "calitate")
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    CompanyRepresentative.company_id,
                    CompanyRepresentative.nume,
                    CompanyRepresentative.calitate,
                ],
                set_=update_cols,
            )
            session.execute(stmt)
            stats.inserted += len(payload)
        else:
            for row in payload:
                existing = (
                    session.query(CompanyRepresentative)
                    .filter(
                        CompanyRepresentative.company_id == row["company_id"],
                        CompanyRepresentative.nume == row["nume"],
                        CompanyRepresentative.calitate == row["calitate"],
                    )
                    .one_or_none()
                )
                if existing is None:
                    session.add(CompanyRepresentative(**row))
                    stats.inserted += 1
                else:
                    for key, value in row.items():
                        setattr(existing, key, value)
                    stats.updated += 1
        session.commit()
        # print(f"Processed batch: {stats.rows_seen} rows, {stats.inserted} inserted, {stats.updated} updated, {stats.skipped_no_company} skipped (no company), {stats.errors} errors")
    return stats


def import_company_representatives(file_path: Path, batch_size: int = 5000, truncate: bool = False) -> ImportStats:
    init_postgres()

    if truncate:
        with SessionLocal() as session:
            session.execute(delete(CompanyRepresentative))
            session.commit()

    total = ImportStats()
    for batch, invalid_rows in _read_batches(file_path, batch_size=batch_size):
        batch_stats = _upsert_batch(batch, invalid_rows)
        total.merge(batch_stats)
        print(
            f"Progress: {total.rows_seen} rows seen, {total.inserted} inserted, "
            f"{total.updated} updated, {total.skipped_no_company} skipped (no company), "
            f"{total.errors} errors"
        )
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import administratori/reprezentanti legali (company_representatives) din ONRC od_reprezentanti_legali.csv"
    )
    parser.add_argument("--file", required=True, help="Calea catre fisierul od_reprezentanti_legali.csv")
    parser.add_argument("--batch-size", type=int, default=5000, help="Numarul de randuri per batch")
    parser.add_argument("--truncate", action="store_true", help="Sterge tabela inainte de import")
    args = parser.parse_args()

    stats = import_company_representatives(Path(args.file), batch_size=args.batch_size, truncate=args.truncate)
    print(f"Import finalizat. Randuri citite: {stats.rows_seen}")
    print(f"Inserate: {stats.inserted}")
    print(f"Actualizate: {stats.updated}")
    print(f"Ignorate (companie negasita dupa nr. inmatriculare): {stats.skipped_no_company}")
    print(f"Erori / randuri invalide: {stats.errors}")


if __name__ == "__main__":
    main()
