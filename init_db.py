"""
Shim de compatibilitate — delega catre scripts/init_caen_db.py.
Păstrat la rădăcina proiectului astfel încât `python init_db.py` să funcționeze
în continuare conform documentației existente (AGENTS.md, CLAUDE.md).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scripts.init_caen_db import init_db  # noqa: E402

DB_PATH = os.getenv("DB_PATH", "caen.db")

if __name__ == "__main__":
    init_db()
