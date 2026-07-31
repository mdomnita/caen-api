import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company
from routers.company_utils import clean_text, normalize_company_name, parse_cui, parse_ro_date


SOURCE_COLUMNS = {
    "name": "DENUMIRE",
    "cui": "CUI",
    "registration_number": "COD_INMATRICULARE",
    "registration_date": "DATA_INMATRICULARE",
    "euid": "EUID",
    "legal_form": "FORMA_JURIDICA",
    "country": "ADR_TARA",
    "county": "ADR_JUDET",
    "locality": "ADR_LOCALITATE",
    "street": "ADR_DEN_STRADA",
    "street_number": "ADR_NR_STRADA",
    "building": "ADR_BLOC",
    "staircase": "ADR_SCARA",
    "floor": "ADR_ETAJ",
    "apartment": "ADR_APARTAMENT",
    "postal_code": "ADR_COD_POSTAL",
    "sector": "ADR_SECTOR",
    "address_extra": "ADR_COMPLETARE",
    "website": "WEB",
    "parent_company_country": "TARA_FIRMA_MAMA",
}


@dataclass
class ImportStats:
    rows_seen: int = 0
    inserted: int = 0
    skipped: int = 0
    duplicates: int = 0
    errors: int = 0

    @property
    def processed(self) -> int:
        return self.inserted

    def merge(self, other: "ImportStats") -> None:
        self.rows_seen += other.rows_seen
        self.inserted += other.inserted
        self.skipped += other.skipped
        self.duplicates += other.duplicates
        self.errors += other.errors


def _row_to_payload(row: dict[str, str]) -> dict | None:
    name = clean_text(row.get(SOURCE_COLUMNS["name"]))
    cui = parse_cui(row.get(SOURCE_COLUMNS["cui"]))
    if not name or cui is None or cui <= 0:
        return None

    payload = {
        "name": name,
        "normalized_name": normalize_company_name(name),
        "cui": cui,
        "registration_number": clean_text(row.get(SOURCE_COLUMNS["registration_number"])),
        "registration_date": parse_ro_date(row.get(SOURCE_COLUMNS["registration_date"])),
        "euid": clean_text(row.get(SOURCE_COLUMNS["euid"])),
        "legal_form": clean_text(row.get(SOURCE_COLUMNS["legal_form"])),
        "country": clean_text(row.get(SOURCE_COLUMNS["country"])),
        "county": clean_text(row.get(SOURCE_COLUMNS["county"])),
        "locality": clean_text(row.get(SOURCE_COLUMNS["locality"])),
        "street": clean_text(row.get(SOURCE_COLUMNS["street"])),
        "street_number": clean_text(row.get(SOURCE_COLUMNS["street_number"])),
        "building": clean_text(row.get(SOURCE_COLUMNS["building"])),
        "staircase": clean_text(row.get(SOURCE_COLUMNS["staircase"])),
        "floor": clean_text(row.get(SOURCE_COLUMNS["floor"])),
        "apartment": clean_text(row.get(SOURCE_COLUMNS["apartment"])),
        "postal_code": clean_text(row.get(SOURCE_COLUMNS["postal_code"])),
        "sector": clean_text(row.get(SOURCE_COLUMNS["sector"])),
        "address_extra": clean_text(row.get(SOURCE_COLUMNS["address_extra"])),
        "website": clean_text(row.get(SOURCE_COLUMNS["website"])),
        "parent_company_country": clean_text(row.get(SOURCE_COLUMNS["parent_company_country"])),
    }
    return payload


def _prepare_batch(rows: list[dict[str, str]]) -> tuple[list[dict], ImportStats]:
    stats = ImportStats(rows_seen=len(rows))
    batch: list[dict] = []

    for row in rows:
        payload = _row_to_payload(row)
        if payload is None:
            stats.errors += 1
            continue
        batch.append(payload)

    deduped_batch = _dedupe_batch_by_cui(batch)
    stats.duplicates = len(batch) - len(deduped_batch)
    return deduped_batch, stats


def _read_batches(file_path: Path, batch_size: int):
    raw_batch: list[dict[str, str]] = []
    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="^", quoting=csv.QUOTE_NONE)
        for row in reader:
            raw_batch.append(row)
            if len(raw_batch) >= batch_size:
                yield _prepare_batch(raw_batch)
                raw_batch = []
    if raw_batch:
        yield _prepare_batch(raw_batch)


def _dedupe_batch_by_cui(batch: list[dict]) -> list[dict]:
    deduped: dict[int, dict] = {}
    for row in batch:
        deduped[row["cui"]] = row
    return list(deduped.values())


def _insert_new_batch(batch: list[dict], stats: ImportStats) -> ImportStats:
    if not batch:
        return stats

    with SessionLocal() as session:
        existing_cuis = set(
            session.scalars(
                select(Company.cui).where(Company.cui.in_([row["cui"] for row in batch]))
            )
        )
        new_rows = [row for row in batch if row["cui"] not in existing_cuis]
        stats.skipped += len(batch) - len(new_rows)
        stats.inserted += len(new_rows)

        if new_rows:
            bind = session.get_bind()
            if bind.dialect.name == "postgresql":
                stmt = pg_insert(Company).values(new_rows)
                session.execute(stmt.on_conflict_do_nothing(index_elements=[Company.cui]))
            else:
                session.add_all(Company(**row) for row in new_rows)
        session.commit()
    return stats


def import_companies(file_path: Path, batch_size: int = 1000, truncate: bool = False) -> ImportStats:
    init_postgres()

    if truncate:
        with SessionLocal() as session:
            session.execute(delete(Company))
            session.commit()

    total = ImportStats()
    for batch, batch_stats in _read_batches(file_path, batch_size=batch_size):
        total.merge(_insert_new_batch(batch, batch_stats))
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import firme ONRC noi in PostgreSQL (firmele cu CUI deja existent sunt sarite; "
        "pentru actualizarea lor foloseste scripts/update_companies.py)"
    )
    parser.add_argument("--file", required=True, help="Calea catre fisierul sursa ONRC")
    parser.add_argument("--batch-size", type=int, default=1000, help="Numarul de randuri per batch")
    parser.add_argument("--truncate", action="store_true", help="Sterge tabela inainte de import")
    args = parser.parse_args()

    stats = import_companies(Path(args.file), batch_size=args.batch_size, truncate=args.truncate)
    print(f"Import finalizat. Randuri citite: {stats.rows_seen}")
    print(f"Inserate (firme noi): {stats.inserted}")
    print(f"Sarite (CUI deja existent): {stats.skipped}")
    print(f"Duplicate in fisier: {stats.duplicates}")
    print(f"Erori / randuri ignorate: {stats.errors}")


if __name__ == "__main__":
    main()