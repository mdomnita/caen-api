#!/usr/bin/env python3
"""
Import CAEN Rev.2 clase si tabela de corespondenta CAEN v2 -> CAEN v3
din fisierele CSV locale in SQLite.

Ruleaza din radacina repo-ului:
    python scripts/init_caen_v2_db.py
"""
import csv
import os
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CSV_V2_PATH = REPO_ROOT / "temp" / "caen_v2_clase.csv"
CSV_CORESP_PATH = REPO_ROOT / "temp" / "caen_corespondenta_v2_v3.csv"
SQLITE_DB = os.getenv("SQLITE_DB", str(REPO_ROOT / "caen.db"))

_DDL = """
DROP TABLE IF EXISTS caen_corespondenta;
DROP TABLE IF EXISTS caen_v2;

CREATE TABLE caen_v2 (
    cod      TEXT PRIMARY KEY,
    denumire TEXT NOT NULL
);

CREATE TABLE caen_corespondenta (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    cod_v2            TEXT NULL REFERENCES caen_v2(cod),
    cod_v3            TEXT NOT NULL REFERENCES clase(cod),
    tip_corespondenta TEXT NOT NULL CHECK (tip_corespondenta IN
                       ('NESCHIMBAT','RECODIFICARE','DETALIERE','AGREGARE','MIXT','NOU'))
);

CREATE INDEX idx_coresp_v2 ON caen_corespondenta(cod_v2);
CREATE INDEX idx_coresp_v3 ON caen_corespondenta(cod_v3);
"""


def _read_v2_rows(csv_path: Path) -> list[tuple]:
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [
            (row["cod_caen_v2"].strip(), row["denumire_caen_v2"].strip())
            for row in reader
        ]


def _read_corespondenta_rows(csv_path: Path) -> list[tuple]:
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [
            (
                row["cod_caen_v2"].strip() or None,
                row["cod_caen_v3"].strip(),
                row["tip_corespondenta"].strip(),
            )
            for row in reader
        ]


def init_caen_v2_db(
    csv_v2_path: Path | None = None, csv_coresp_path: Path | None = None
) -> tuple[int, int]:
    v2_path = csv_v2_path or CSV_V2_PATH
    coresp_path = csv_coresp_path or CSV_CORESP_PATH

    if not v2_path.exists():
        raise FileNotFoundError(f"CSV not found: {v2_path}")
    if not coresp_path.exists():
        raise FileNotFoundError(f"CSV not found: {coresp_path}")

    print(f"Database : {SQLITE_DB}")
    print(f"CSV v2   : {v2_path}")
    print(f"CSV coresp: {coresp_path}")

    conn = sqlite3.connect(SQLITE_DB)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_DDL)

    v2_rows = _read_v2_rows(v2_path)
    conn.executemany("INSERT INTO caen_v2 (cod, denumire) VALUES (?, ?)", v2_rows)

    coresp_rows = _read_corespondenta_rows(coresp_path)
    conn.executemany(
        "INSERT INTO caen_corespondenta (cod_v2, cod_v3, tip_corespondenta) VALUES (?, ?, ?)",
        coresp_rows,
    )

    conn.commit()
    conn.close()

    print(f"Finished. CAEN v2 imported: {len(v2_rows)}, corespondente: {len(coresp_rows)}")
    return len(v2_rows), len(coresp_rows)


if __name__ == "__main__":
    init_caen_v2_db()
