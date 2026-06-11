import argparse
import csv
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete
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


def _read_batches(file_path: Path, batch_size: int):
    batch: list[dict] = []
    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="^")
        for row in reader:
            payload = _row_to_payload(row)
            if payload is None:
                continue
            batch.append(payload)
            if len(batch) >= batch_size:
                yield batch
                batch = []
    if batch:
        yield batch


def _dedupe_batch_by_cui(batch: list[dict]) -> list[dict]:
    deduped: dict[int, dict] = {}
    for row in batch:
        deduped[row["cui"]] = row
    return list(deduped.values())


def _upsert_batch(batch: list[dict]) -> int:
    deduped_batch = _dedupe_batch_by_cui(batch)

    with SessionLocal() as session:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            stmt = pg_insert(Company).values(deduped_batch)
            update_columns = {
                column: getattr(stmt.excluded, column)
                for column in deduped_batch[0].keys()
                if column != "cui"
            }
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[Company.cui],
                    set_=update_columns,
                )
            )
        else:
            for row in deduped_batch:
                existing = session.query(Company).filter(Company.cui == row["cui"]).one_or_none()
                if existing is None:
                    session.add(Company(**row))
                    continue
                for key, value in row.items():
                    setattr(existing, key, value)
        session.commit()
    return len(batch)


def import_companies(file_path: Path, batch_size: int = 1000, truncate: bool = False) -> int:
    init_postgres()

    if truncate:
        with SessionLocal() as session:
            session.execute(delete(Company))
            session.commit()

    total = 0
    for batch in _read_batches(file_path, batch_size=batch_size):
        total += _upsert_batch(batch)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Import firme ONRC in PostgreSQL")
    parser.add_argument("--file", required=True, help="Calea catre fisierul sursa ONRC")
    parser.add_argument("--batch-size", type=int, default=1000, help="Numarul de randuri per batch")
    parser.add_argument("--truncate", action="store_true", help="Sterge tabela inainte de import")
    args = parser.parse_args()

    imported = import_companies(Path(args.file), batch_size=args.batch_size, truncate=args.truncate)
    print(f"Import finalizat. Randuri procesate: {imported}")


if __name__ == "__main__":
    main()