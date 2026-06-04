#!/usr/bin/env python3
"""
Incremental update of BNR exchange rates in the database.

Checks the latest date already imported, then downloads and inserts
only the records that are newer. The current year's XML cache is
always invalidated so today's rates are fetched fresh.

Run from the repo root:
    python scripts/update_exchange_db.py
"""
import sqlite3
import sys
from datetime import date as _Date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.init_exchange_db import (
    DB_PATH,
    TEMP_XML_DIR,
    TEMP_CSV_DIR,
    _download,
    _parse,
    _to_csv,
)

import requests


def update_exchange_db():
    TEMP_XML_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_CSV_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    row = conn.execute("SELECT MAX(data) AS d FROM cursuri_valutare").fetchone()
    latest_date = row["d"] if row else None

    if not latest_date:
        print("Baza de date nu contine date valutare. Ruleaza init_exchange_db.py pentru initializare completa.")
        conn.close()
        sys.exit(1)

    print(f"Database  : {DB_PATH}")
    print(f"Ultima data in DB : {latest_date}")

    current_year = _Date.today().year
    start_year = int(latest_date[:4])

    # The current year's XML is published daily — always fetch it fresh.
    current_xml = TEMP_XML_DIR / f"nbrfxrates{current_year}.xml"
    if current_xml.exists():
        current_xml.unlink()
        print(f"Cache invalidat : nbrfxrates{current_year}.xml")

    total_new = 0
    for year in range(start_year, current_year + 1):
        print(f"\nYear {year}:")
        try:
            xml_path = _download(year)
            all_rows = _parse(xml_path)
            new_rows = [r for r in all_rows if r[0] > latest_date]

            _to_csv(all_rows, year)

            if not new_rows:
                print(f"  Nicio data noua.")
                continue

            conn.executemany(
                "INSERT OR REPLACE INTO cursuri_valutare"
                " (data, valuta, curs, multiplicator) VALUES (?,?,?,?)",
                new_rows,
            )
            conn.commit()
            print(f"  {len(new_rows)} randuri noi (din {len(all_rows)} in fisier).")
            total_new += len(new_rows)

        except requests.HTTPError as exc:
            print(f"  WARNING: {exc} — skipping", file=sys.stderr)

    conn.close()

    if total_new:
        print(f"\nActualizare completa. {total_new} randuri noi importate.")
    else:
        print(f"\nBaza de date este deja actualizata (ultima data: {latest_date}).")


if __name__ == "__main__":
    update_exchange_db()
