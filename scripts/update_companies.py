import argparse
from dataclasses import dataclass
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company
from scripts.import_companies import _read_batches


@dataclass
class UpdateStats:
    rows_seen: int = 0
    updated: int = 0
    skipped: int = 0
    duplicates: int = 0
    errors: int = 0

    def merge(self, other: "UpdateStats") -> None:
        self.rows_seen += other.rows_seen
        self.updated += other.updated
        self.skipped += other.skipped
        self.duplicates += other.duplicates
        self.errors += other.errors


def _update_existing_batch(batch: list[dict], stats: UpdateStats) -> UpdateStats:
    if not batch:
        return stats

    with SessionLocal() as session:
        existing_companies = {
            company.cui: company
            for company in session.scalars(
                select(Company).where(Company.cui.in_([row["cui"] for row in batch]))
            )
        }

        for row in batch:
            existing = existing_companies.get(row["cui"])
            if existing is None:
                stats.skipped += 1
                continue
            for key, value in row.items():
                setattr(existing, key, value)
            stats.updated += 1

        session.commit()
    return stats


def update_companies(file_path: Path, batch_size: int = 1000) -> UpdateStats:
    init_postgres()

    total = UpdateStats()
    for batch, batch_stats in _read_batches(file_path, batch_size=batch_size):
        stats = UpdateStats(
            rows_seen=batch_stats.rows_seen,
            duplicates=batch_stats.duplicates,
            errors=batch_stats.errors,
        )
        total.merge(_update_existing_batch(batch, stats))
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Actualizeaza firmele ONRC deja existente in PostgreSQL "
        "(firmele fara CUI existent sunt sarite; pentru importul lor foloseste scripts/import_companies.py)"
    )
    parser.add_argument("--file", required=True, help="Calea catre fisierul sursa ONRC")
    parser.add_argument("--batch-size", type=int, default=1000, help="Numarul de randuri per batch")
    args = parser.parse_args()

    stats = update_companies(Path(args.file), batch_size=args.batch_size)
    print(f"Actualizare finalizata. Randuri citite: {stats.rows_seen}")
    print(f"Actualizate: {stats.updated}")
    print(f"Sarite (CUI inexistent): {stats.skipped}")
    print(f"Duplicate in fisier: {stats.duplicates}")
    print(f"Erori / randuri ignorate: {stats.errors}")


if __name__ == "__main__":
    main()
