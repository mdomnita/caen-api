#!/usr/bin/env python3
"""
Download BNR exchange rate XML files for the last 10 years,
convert to CSV, and import into the SQLite database.

Run from the repo root:
    python scripts/init_exchange_db.py
"""
import csv
import os
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

YEARS = range(2005, 2027)
BNR_URL = "https://www.bnr.ro/files/xml/years/nbrfxrates{year}.xml"
BNR_NS = {"b": "http://www.bnr.ro/xsd"}

REPO_ROOT = Path(__file__).parent.parent
TEMP_XML_DIR = REPO_ROOT / "temp" / "exchange_rates" / "xml"
TEMP_CSV_DIR = REPO_ROOT / "temp" / "exchange_rates" / "csv"
DB_PATH = os.getenv("DB_PATH", str(REPO_ROOT / "caen.db"))

_DDL = """
CREATE TABLE IF NOT EXISTS cursuri_valutare (
    data         TEXT    NOT NULL,
    valuta       TEXT    NOT NULL,
    curs         REAL    NOT NULL,
    multiplicator INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (data, valuta)
);
CREATE INDEX IF NOT EXISTS idx_cv_valuta ON cursuri_valutare (valuta);
CREATE INDEX IF NOT EXISTS idx_cv_data   ON cursuri_valutare (data);
"""


def _download(year: int) -> Path:
    dest = TEMP_XML_DIR / f"nbrfxrates{year}.xml"
    if dest.exists():
        print(f"  [cached]  {dest.name}")
        return dest
    url = BNR_URL.format(year=year)
    print(f"  [fetch]   {url}")
    r = requests.get(url, timeout=30, verify=False)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def _parse(xml_path: Path) -> list[tuple]:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    rows = []
    for cube in root.findall(".//b:Cube", BNR_NS):
        date = cube.get("date")
        for el in cube.findall("b:Rate", BNR_NS):
            if not el.text or el.text.strip() == "-":
                continue
            currency = el.get("currency")
            multiplier = int(el.get("multiplier", 1))
            rate = float(el.text)
            rows.append((date, currency, rate, multiplier))
    return rows


def _to_csv(rows: list[tuple], year: int) -> Path:
    dest = TEMP_CSV_DIR / f"nbrfxrates{year}.csv"
    with open(dest, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["data", "valuta", "curs", "multiplicator"])
        w.writerows(rows)
    return dest


def _import(csv_path: Path, conn: sqlite3.Connection) -> int:
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = [
            (r["data"], r["valuta"], float(r["curs"]), int(r["multiplicator"]))
            for r in csv.DictReader(f)
        ]
    conn.executemany(
        "INSERT OR REPLACE INTO cursuri_valutare (data, valuta, curs, multiplicator) VALUES (?,?,?,?)",
        rows,
    )
    return len(rows)


def init_exchange_db():
    TEMP_XML_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_CSV_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Database : {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    for stmt in _DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()

    total = 0
    for year in YEARS:
        print(f"\nYear {year}:")
        try:
            xml_path = _download(year)
            rows = _parse(xml_path)
            csv_path = _to_csv(rows, year)
            n = _import(csv_path, conn)
            conn.commit()
            print(f"  {n} rate records written to {csv_path.name}")
            total += n
        except requests.HTTPError as exc:
            print(f"  WARNING: {exc} — skipping", file=sys.stderr)

    conn.close()
    print(f"\nFinished. Total rows imported: {total}")


if __name__ == "__main__":
    init_exchange_db()
