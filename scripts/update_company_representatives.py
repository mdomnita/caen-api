"""Add and backfill inferred person identifiers on existing representative rows.

The identifier is deterministic but is not an official ONRC identifier. Run this once
after deploying the model change; rerunning it is safe.
"""
import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, select, text

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company, CompanyRepresentative
from scripts.import_company_representatives import build_person_identifier


@dataclass
class UpdateStats:
    processed: int = 0
    updated: int = 0


def ensure_person_identifier_schema() -> None:
    with SessionLocal() as session:
        bind = session.get_bind()

    with bind.begin() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("company_representatives")}
        if "person_identifier" not in columns:
            connection.execute(
                text("ALTER TABLE company_representatives ADD COLUMN person_identifier VARCHAR(64)")
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_company_representatives_person_identifier "
                "ON company_representatives (person_identifier)"
            )
        )


def update_company_representatives(batch_size: int = 5000) -> UpdateStats:
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    init_postgres()
    ensure_person_identifier_schema()
    stats = UpdateStats()
    last_id = 0

    while True:
        with SessionLocal() as session:
            rows = session.execute(
                select(CompanyRepresentative, Company.registration_number)
                .join(Company, Company.id == CompanyRepresentative.company_id)
                .where(CompanyRepresentative.id > last_id)
                .order_by(CompanyRepresentative.id)
                .limit(batch_size)
            ).all()
            if not rows:
                break

            for representative, registration_number in rows:
                representative.person_identifier = build_person_identifier(
                    registration_number=registration_number,
                    nume=representative.nume,
                    data_nasterii=representative.data_nasterii,
                    localitate_nasterii=representative.localitate_nasterii,
                    judet_nasterii=representative.judet_nasterii,
                    tara_nasterii=representative.tara_nasterii,
                )
                stats.updated += 1

            stats.processed += len(rows)
            last_id = rows[-1][0].id
            session.commit()
            print(f"Progress: {stats.processed} processed, {stats.updated} updated")

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add and backfill person identifiers for company representatives"
    )
    parser.add_argument("--batch-size", type=int, default=5000)
    args = parser.parse_args()

    stats = update_company_representatives(batch_size=args.batch_size)
    print(f"Update complete. Rows processed: {stats.processed}; updated: {stats.updated}")


if __name__ == "__main__":
    main()
