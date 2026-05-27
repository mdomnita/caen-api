"""
Inițializare tabele SIRUTA în baza de date SQLite din fișierul siruta_toate.csv.

Schema:
  judete     – județe (41 județe + municipiul București)
  localitati – toate UAT-urile: municipii, orașe, comune, sectoare

Tipuri UAT (tip_cod / tip_abrev):
  11 / CJ – Consiliu Județean   (rând administrativ, nu o localitate propriu-zisă)
  12 / M  – Municipiu
  13 / O  – Oraș
  14 / C  – Comună
  15 / B  – Municipiul București
  16 / S  – Sector (București)

Rulează independent față de init_caen_db.py; ambele scriu în același DB_PATH.
"""
import csv
import os
import re
import sqlite3

DB_PATH = os.getenv("DB_PATH", "caen.db")
CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "siruta_toate.csv")

TIP_DENUMIRE: dict[str, str] = {
    "11": "Consiliu Județean",
    "12": "Municipiu",
    "13": "Oraș",
    "14": "Comună",
    "15": "Municipiul București",
    "16": "Sector",
}


def _normalize(name: str) -> str:
    """Elimină spațiile multiple și face trim."""
    return re.sub(r" {2,}", " ", name).strip()


def init_siruta() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript("""
        DROP TABLE IF EXISTS localitati;
        DROP TABLE IF EXISTS judete;

        CREATE TABLE judete (
            cod_judet INTEGER PRIMARY KEY,
            denumire  TEXT NOT NULL
        );

        CREATE TABLE localitati (
            cod_siruta   INTEGER PRIMARY KEY,
            denumire     TEXT    NOT NULL,
            tip_cod      INTEGER NOT NULL,
            tip_abrev    TEXT    NOT NULL,
            tip_denumire TEXT    NOT NULL,
            cod_judet    INTEGER NOT NULL REFERENCES judete(cod_judet)
        );

        -- Indecși pentru căutare după nume, filtrare după județ și tip
        CREATE INDEX idx_localitati_denumire ON localitati(denumire);
        CREATE INDEX idx_localitati_judet    ON localitati(cod_judet);
        CREATE INDEX idx_localitati_tip      ON localitati(tip_cod);
    """)

    judete_vazute: set[int] = set()

    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cod_judet = int(row["cod_judet"])
            denumire_judet = _normalize(row["denumire_judet"])

            if cod_judet not in judete_vazute:
                judete_vazute.add(cod_judet)
                conn.execute(
                    "INSERT OR IGNORE INTO judete (cod_judet, denumire) VALUES (?, ?)",
                    (cod_judet, denumire_judet),
                )

            tip_cod_str = row["tip_uat_cod"].strip()
            tip_abrev = row["tip_uat_abrev"].strip()
            tip_denumire = TIP_DENUMIRE.get(tip_cod_str, tip_abrev)
            cod_siruta = int(row["cod_siruta"])
            denumire_uat = _normalize(row["denumire_uat"])

            conn.execute(
                """
                INSERT OR IGNORE INTO localitati
                    (cod_siruta, denumire, tip_cod, tip_abrev, tip_denumire, cod_judet)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (cod_siruta, denumire_uat, int(tip_cod_str), tip_abrev, tip_denumire, cod_judet),
            )

    conn.commit()

    count_j = conn.execute("SELECT COUNT(*) FROM judete").fetchone()[0]
    count_l = conn.execute("SELECT COUNT(*) FROM localitati").fetchone()[0]
    conn.close()
    print(f"SIRUTA incarcat: {count_j} judete, {count_l} localitati in '{DB_PATH}'")


if __name__ == "__main__":
    init_siruta()
