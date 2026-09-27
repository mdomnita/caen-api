"""
Initializare completa a bazei de date.
Ruleaza pipeline-ul centralizat de metadate si incarcari de date, pentru un reinstalare/
refresh rapid fara a mai invoca scripturile manual, in ordine.

Utilizare:
    python init_db.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auth import ensure_observability_tables
from scripts.data_pipeline import run_pipeline

if __name__ == "__main__":
    print("=== Pipeline de date centralizat ===")
    run_pipeline()

    print("\n=== Observabilitate API ===")
    ensure_observability_tables()

    print("\nInitializare completa.")
