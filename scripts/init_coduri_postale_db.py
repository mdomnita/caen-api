"""
Initializare tabela coduri_postale in baza de date SQLite, din fisierele CSV
(convertite din .xls) ale Postei Romane, publicate pe data.gov.ro:
https://data.gov.ro/dataset/coduri-postale-romania

Surse (varianta cu SIRUTA, cod_siruta e preluat direct din sursa, fara
potrivire dupa nume):
  infocod-mai-2016_siruta.csv       - strazi Bucuresti (are sector, nu are judet)
  infocod-mai-2016_orase_siruta.csv - localitati > 50.000 locuitori, nivel strada
  infocod-mai-2016_sate_siruta.csv  - localitati < 50.000 locuitori, doar nivel localitate

Rezolva cod_judet prin potrivire dupa nume (fara diacritice, uppercase) fata de
tabela `judete`, deci trebuie rulat dupa init_siruta_db.py (in aceeasi baza).

Rulează independent față de celelalte scripturi init_*; toate scriu în același SQLITE_DB.
"""
import csv
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from text_normalization import normalize_search, normalize_whitespace

SQLITE_DB = os.getenv("SQLITE_DB", "caen.db")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "temp", "posta-romana", "coduri-postale-romania")

BUCURESTI_CSV = os.path.join(DATA_DIR, "infocod-mai-2016_siruta.csv")
ORASE_CSV = os.path.join(DATA_DIR, "infocod-mai-2016_orase_siruta.csv")
SATE_CSV = os.path.join(DATA_DIR, "infocod-mai-2016_sate_siruta.csv")

BUCURESTI_COD_JUDET = 42
BUCURESTI_JUDET_RAW = "București"
BUCURESTI_LOCALITATE_RAW = "București"

_LOCALITATE_PARINTE_RE = re.compile(r"^(.*)\s+\((.*)\)$")
_NUMAR_TOKEN_RE = re.compile(r"^(\d+)([A-Z]?)(?:-(\d+|T)([A-Z]?))?$")


def _int_or_none(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


def _split_localitate(raw: str) -> tuple[str, str | None]:
    raw = normalize_whitespace(raw)
    m = _LOCALITATE_PARINTE_RE.match(raw)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return raw, None


def _parse_numar(raw: str | None) -> tuple[str | None, int | None, int | None, int, str | None]:
    """Best-effort parse of the free-text 'Numar'/'Numar/Bloc' field.

    Returns (numar_tip, numar_min, numar_max, numar_open_ended, numar_paritate).
    Anything not cleanly parseable (multi-group cells, 'bl.' blocks, letter
    suffixes) leaves the numeric fields None rather than guessing — numar_raw
    is always kept verbatim by the caller.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, None, None, 0, None

    groups = [g.strip() for g in raw.split(";") if g.strip()]
    if len(groups) != 1:
        return None, None, None, 0, None
    group = groups[0]

    if group.lower().startswith("bl."):
        return "bl", None, None, 0, None
    if not group.lower().startswith("nr."):
        return None, None, None, 0, None

    body = group[3:].strip()
    tokens = [t.strip() for t in body.split(",") if t.strip()]
    if len(tokens) != 1:
        return "nr", None, None, 0, None

    m = _NUMAR_TOKEN_RE.match(tokens[0])
    if not m:
        return "nr", None, None, 0, None

    low_s, low_suffix, high_s, high_suffix = m.groups()
    if low_suffix or high_suffix:
        return "nr", None, None, 0, None

    low = int(low_s)
    if high_s is None:
        paritate = "impar" if low % 2 else "par"
        return "nr", low, low, 0, paritate
    if high_s == "T":
        paritate = "impar" if low % 2 else "par"
        return "nr", low, None, 1, paritate

    high = int(high_s)
    paritate = None
    if (low % 2) == (high % 2):
        paritate = "impar" if low % 2 else "par"
    return "nr", low, high, 0, paritate


def _load_judete_lookup(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT cod_judet, denumire FROM judete").fetchall()
    return {normalize_search(denumire): cod_judet for cod_judet, denumire in rows}


def init_coduri_postale() -> None:
    conn = sqlite3.connect(SQLITE_DB)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript("""
        DROP TABLE IF EXISTS coduri_postale;

        CREATE TABLE coduri_postale (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            cod_postal              TEXT    NOT NULL,

            judet_raw               TEXT,
            judet_norm               TEXT    NOT NULL,
            cod_judet                INTEGER,

            localitate_raw           TEXT,
            localitate_norm          TEXT    NOT NULL,
            localitate_parinte_raw   TEXT,
            localitate_parinte_norm  TEXT,
            cod_siruta                INTEGER,
            siruta_sirsup             INTEGER,
            siruta_niv                 INTEGER,

            sector                   INTEGER,

            tip_artera_raw           TEXT,
            tip_artera_norm          TEXT,
            strada_raw                TEXT,
            strada_norm                TEXT,

            numar_raw                 TEXT,
            numar_tip                  TEXT,
            numar_min                  INTEGER,
            numar_max                  INTEGER,
            numar_open_ended           INTEGER NOT NULL DEFAULT 0,
            numar_paritate              TEXT,

            oficiu_distribuire         TEXT,
            sursa                       TEXT    NOT NULL,
            sursa_versiune              TEXT    NOT NULL DEFAULT 'infocod-mai-2016'
        );

        CREATE INDEX idx_cp_cod_postal        ON coduri_postale(cod_postal);
        CREATE INDEX idx_cp_judet_norm        ON coduri_postale(judet_norm);
        CREATE INDEX idx_cp_localitate_norm   ON coduri_postale(localitate_norm);
        CREATE INDEX idx_cp_strada_norm       ON coduri_postale(strada_norm);
        CREATE INDEX idx_cp_cod_siruta        ON coduri_postale(cod_siruta);
        CREATE INDEX idx_cp_judet_localitate  ON coduri_postale(judet_norm, localitate_norm);
        CREATE INDEX idx_cp_localitate_strada ON coduri_postale(localitate_norm, strada_norm);
    """)

    judete_lookup = _load_judete_lookup(conn)

    stats = {"bucuresti": 0, "oras": 0, "sat": 0}
    cod_judet_resolved = 0
    cod_judet_missing: set[str] = set()
    cod_siruta_missing = 0

    insert_sql = """
        INSERT INTO coduri_postale (
            cod_postal, judet_raw, judet_norm, cod_judet,
            localitate_raw, localitate_norm, localitate_parinte_raw, localitate_parinte_norm,
            cod_siruta, siruta_sirsup, siruta_niv, sector,
            tip_artera_raw, tip_artera_norm, strada_raw, strada_norm,
            numar_raw, numar_tip, numar_min, numar_max, numar_open_ended, numar_paritate,
            oficiu_distribuire, sursa
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def resolve_cod_judet(judet_raw: str, judet_norm: str) -> int | None:
        nonlocal cod_judet_resolved
        cod_judet = judete_lookup.get(judet_norm)
        if cod_judet is not None:
            cod_judet_resolved += 1
        else:
            cod_judet_missing.add(judet_raw)
        return cod_judet

    # --- Bucuresti (strazi) ---------------------------------------------
    with open(BUCURESTI_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            localitate_norm = normalize_search(BUCURESTI_LOCALITATE_RAW)
            tip_artera_raw = normalize_whitespace(row["Tip artera"])
            strada_raw = normalize_whitespace(row["Denumire artera"])
            numar_tip, numar_min, numar_max, numar_open_ended, numar_paritate = _parse_numar(row["Numar"])
            cod_siruta = _int_or_none(row["SIRUTA SECTOR"])
            if cod_siruta is None:
                cod_siruta_missing += 1

            conn.execute(insert_sql, (
                row["Codpostal"], BUCURESTI_JUDET_RAW, normalize_search(BUCURESTI_JUDET_RAW), BUCURESTI_COD_JUDET,
                BUCURESTI_LOCALITATE_RAW, localitate_norm, None, None,
                cod_siruta, _int_or_none(row["SIRSUP"]), _int_or_none(row["NIV"]), _int_or_none(row["Sector"]),
                tip_artera_raw, normalize_search(tip_artera_raw), strada_raw, normalize_search(strada_raw),
                row["Numar"] or None, numar_tip, numar_min, numar_max, numar_open_ended, numar_paritate,
                row["Oficiu distribuire"] or None, "bucuresti",
            ))
            stats["bucuresti"] += 1

    # --- Orase (> 50.000 locuitori, nivel strada) ------------------------
    with open(ORASE_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            judet_raw = normalize_whitespace(row["Judet"])
            judet_norm = normalize_search(judet_raw)
            cod_judet = resolve_cod_judet(judet_raw, judet_norm)

            localitate_raw, localitate_parinte_raw = _split_localitate(row["Localitate"])
            localitate_norm = normalize_search(localitate_raw)
            localitate_parinte_norm = normalize_search(localitate_parinte_raw) if localitate_parinte_raw else None

            tip_artera_raw = normalize_whitespace(row["Tip artera"])
            strada_raw = normalize_whitespace(row["Denumire artera"])
            numar_tip, numar_min, numar_max, numar_open_ended, numar_paritate = _parse_numar(row["Numar/Bloc"])
            cod_siruta = _int_or_none(row["SIRUTA"])
            if cod_siruta is None:
                cod_siruta_missing += 1

            conn.execute(insert_sql, (
                row["Codpostal"], judet_raw, judet_norm, cod_judet,
                localitate_raw, localitate_norm, localitate_parinte_raw, localitate_parinte_norm,
                cod_siruta, _int_or_none(row["SIRSUP"]), _int_or_none(row["NIV"]), None,
                tip_artera_raw, normalize_search(tip_artera_raw), strada_raw, normalize_search(strada_raw),
                row["Numar/Bloc"] or None, numar_tip, numar_min, numar_max, numar_open_ended, numar_paritate,
                None, "oras",
            ))
            stats["oras"] += 1

    # --- Sate (< 50.000 locuitori, doar nivel localitate) ----------------
    with open(SATE_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            judet_raw = normalize_whitespace(row["Judet"])
            judet_norm = normalize_search(judet_raw)
            cod_judet = resolve_cod_judet(judet_raw, judet_norm)

            localitate_raw, localitate_parinte_raw = _split_localitate(row["Localitate"])
            localitate_norm = normalize_search(localitate_raw)
            localitate_parinte_norm = normalize_search(localitate_parinte_raw) if localitate_parinte_raw else None

            cod_siruta = _int_or_none(row["SIRUTA"])
            if cod_siruta is None:
                cod_siruta_missing += 1

            conn.execute(insert_sql, (
                row["Codpostal"], judet_raw, judet_norm, cod_judet,
                localitate_raw, localitate_norm, localitate_parinte_raw, localitate_parinte_norm,
                cod_siruta, None, None, None,
                None, None, None, None,
                None, None, None, None, 0, None,
                None, "sat",
            ))
            stats["sat"] += 1

    conn.commit()

    total = sum(stats.values())
    total_judet_rows = stats["oras"] + stats["sat"]
    conn.close()

    print(f"Coduri postale incarcate: {total} ({stats['bucuresti']} bucuresti, {stats['oras']} orase, {stats['sat']} sate) in '{SQLITE_DB}'")
    print(f"cod_judet rezolvat: {cod_judet_resolved}/{total_judet_rows}")
    if cod_judet_missing:
        print(f"cod_judet nerezolvat pentru judetele: {sorted(cod_judet_missing)}")
    print(f"cod_siruta lipsa in sursa: {cod_siruta_missing}/{total}")


if __name__ == "__main__":
    init_coduri_postale()
