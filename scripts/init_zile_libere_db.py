#!/usr/bin/env python3
"""
Import legal public holidays from the local CSV file into SQLite.

Run from the repo root:
    python scripts/init_zile_libere_db.py
"""
import csv
import os
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CSV_PATH = REPO_ROOT / "temp" / "zile_libere_legale_romania_2026.csv"
SQLITE_DB = os.getenv("SQLITE_DB", str(REPO_ROOT / "caen.db"))

_DDL = """
CREATE TABLE IF NOT EXISTS zile_libere (
    data                          TEXT    NOT NULL,
    zi_saptamana                  TEXT    NOT NULL,
    denumire_sarbatoare           TEXT    NOT NULL,
    temei_art_139_codul_muncii    TEXT    NOT NULL,
    cade_in_weekend               INTEGER NOT NULL DEFAULT 0,
    observatii                    TEXT,
    sursa_legala                  TEXT    NOT NULL,
    sursa_calendar                TEXT,
    sursa_verificare_suplimentara TEXT,
    PRIMARY KEY (data, denumire_sarbatoare)
);
CREATE INDEX IF NOT EXISTS idx_zile_libere_data ON zile_libere (data);
"""


def _parse_bool(value: str) -> int:
    return 1 if value.strip().lower() == "da" else 0


def _read_rows(csv_path: Path) -> list[tuple]:
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [
            (
                row["data"].strip(),
                row["zi_saptamana"].strip(),
                row["denumire_sarbatoare"].strip(),
                row["temei_art_139_codul_muncii"].strip(),
                _parse_bool(row["cade_in_weekend"]),
                row["observatii"].strip() or None,
                row["sursa_legala"].strip(),
                row["sursa_calendar_2026"].strip() or None,
                row["sursa_verificare_suplimentara"].strip() or None,
            )
            for row in reader
        ]


def _import(rows: list[tuple], conn: sqlite3.Connection) -> int:
    conn.executemany(
        """
        INSERT OR REPLACE INTO zile_libere (
            data,
            zi_saptamana,
            denumire_sarbatoare,
            temei_art_139_codul_muncii,
            cade_in_weekend,
            observatii,
            sursa_legala,
            sursa_calendar,
            sursa_verificare_suplimentara
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def init_zile_libere_db(csv_path: Path | None = None) -> int:
    source_path = csv_path or CSV_PATH
    if not source_path.exists():
        raise FileNotFoundError(f"CSV not found: {source_path}")

    print(f"Database : {SQLITE_DB}")
    print(f"CSV      : {source_path}")

    conn = sqlite3.connect(SQLITE_DB)
    for stmt in _DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)

    rows = _read_rows(source_path)
    imported = _import(rows, conn)
    conn.commit()
    conn.close()

    print(f"Finished. Total holidays imported: {imported}")
    return imported


if __name__ == "__main__":
    init_zile_libere_db()