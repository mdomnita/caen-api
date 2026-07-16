"""
Initializare completa a bazei de date.
Ruleaza scripturile in ordine:
  1. CAEN Rev.3     — scripts/init_caen_db.py
  2. SIRUTA 2025    — scripts/init_siruta_db.py
  3. Curs valutar   — scripts/init_exchange_db.py
  4. Zile libere    — scripts/init_zile_libere_db.py
  5. Localitati geo — scripts/init_localitati_geo_db.py (necesita LOCALITIES_DATABASE_URL)

Utilizare:
    python init_db.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auth import ensure_observability_tables
from scripts.init_caen_db import init_db
from scripts.init_siruta_db import init_siruta
from scripts.init_exchange_db import init_exchange_db
from scripts.init_zile_libere_db import init_zile_libere_db
from scripts.init_localitati_geo_db import init_localitati_geo_db

if __name__ == "__main__":
    print("=== CAEN Rev.3 ===")
    init_db()

    print("\n=== SIRUTA 2025 ===")
    init_siruta()

    print("\n=== Cursuri valutare BNR ===")
    init_exchange_db()

    print("\n=== Zile libere legale ===")
    init_zile_libere_db()

    print("\n=== Localitati (geo) ===")
    try:
        init_localitati_geo_db()
    except Exception as exc:
        print(f"AVERTISMENT: import localitati_geo esuat ({exc}). Rulati manual dupa configurarea LOCALITIES_DATABASE_URL.")

    print("\n=== Observabilitate API ===")
    ensure_observability_tables()

    print("\nInitializare completa.")
