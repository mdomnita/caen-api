#!/usr/bin/env python3
"""
Download BNR exchange rate XML files for the last 10 years,
convert to CSV, and import into the SQLite database.

Run from the repo root:
    python scripts/init_exchange_db.py
"""
import csv
import email.utils
import os
import sqlite3
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

YEARS = range(2005, 2027)
BNR_URL = "https://curs.bnr.ro/files/xml/years/nbrfxrates{year}.xml"
BNR_NS = {"b": "https://www.bnr.ro/xsd"}  # the XML's actual xmlns=, not its xsi:schemaLocation

REPO_ROOT = Path(__file__).parent.parent
TEMP_XML_DIR = REPO_ROOT / "temp" / "exchange_rates" / "xml"
TEMP_CSV_DIR = REPO_ROOT / "temp" / "exchange_rates" / "csv"
SQLITE_DB = os.getenv("SQLITE_DB", str(REPO_ROOT / "caen.db"))

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


def _download(year: int, force: bool = False) -> Path:
    """Download year's XML, using If-Modified-Since caching unless force is
    True. force must be used for the current (still-changing) year: BNR
    updates it intraday, and a stale local file passing its own mtime back
    as If-Modified-Since can get a 304 that silently hides genuinely new
    rates from the caller (see update_exchange_db.py, which relies on this
    to know whether there's anything new to import).
    """
    dest = TEMP_XML_DIR / f"nbrfxrates{year}.xml"
    url = BNR_URL.format(year=year)

    cached = not force and dest.exists()
    headers = {}
    if cached:
        headers["If-Modified-Since"] = email.utils.formatdate(dest.stat().st_mtime, usegmt=True)
        print(f"  [check]   {url}")
    else:
        print(f"  [fetch]   {url}")

    r = requests.get(url, timeout=30, verify=False, headers=headers)
    if r.status_code == 304 and cached:
        print(f"  [cached]  {dest.name}")
        return dest

    r.raise_for_status()
    dest.write_bytes(r.content)

    last_modified = r.headers.get("Last-Modified")
    if last_modified:
        try:
            modified_at = email.utils.parsedate_to_datetime(last_modified).timestamp()
            os.utime(dest, (modified_at, modified_at))
        except (TypeError, ValueError, OverflowError):
            pass

    print(f"  [{'updated' if cached else 'saved'}]  {dest.name}")
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

    print(f"Database : {SQLITE_DB}")
    conn = sqlite3.connect(SQLITE_DB)
    for stmt in _DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()

    current_year = date.today().year
    total = 0
    for year in YEARS:
        print(f"\nYear {year}:")
        try:
            xml_path = _download(year, force=(year == current_year))
            rows = _parse(xml_path)
            csv_path = _to_csv(rows, year)
            n = _import(csv_path, conn)
            conn.commit()
            print(f"  {n} rate records written to {csv_path.name}")
            total += n
        except Exception as exc:
            # Anything here (bad HTTP status, malformed XML, a bad row,
            # a locked DB, ...) must not take down the whole run -- one
            # year's failure shouldn't stop every later year from being
            # attempted. rollback() discards any partially-executed
            # statements for this year so the next iteration (or a rerun)
            # starts clean rather than riding along in a stale transaction.
            conn.rollback()
            print(f"  WARNING: {exc} — skipping", file=sys.stderr)

    conn.close()
    print(f"\nFinished. Total rows imported: {total}")


if __name__ == "__main__":
    init_exchange_db()
