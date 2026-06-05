"""
Initializare completa a bazei de date.
Ruleaza toate cele trei scripturi in ordine:
  1. CAEN Rev.3   — scripts/init_caen_db.py
  2. SIRUTA 2025  — scripts/init_siruta_db.py
  3. Curs valutar — scripts/init_exchange_db.py

Utilizare:
    python init_db.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scripts.init_caen_db import init_db
from scripts.init_siruta_db import init_siruta
from scripts.init_exchange_db import init_exchange_db
from scripts.init_zile_libere_db import init_zile_libere_db

if __name__ == "__main__":
    print("=== CAEN Rev.3 ===")
    init_db()

    print("\n=== SIRUTA 2025 ===")
    init_siruta()

    print("\n=== Cursuri valutare BNR ===")
    init_exchange_db()

    print("\n=== Zile libere legale ===")
    init_zile_libere_db()

    print("\nInitializare completa.")
