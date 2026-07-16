"""
Inițializare tabelă localitati_geo în baza de date SQLite, din tabela PostgreSQL/PostGIS
public.localities (gid, nume_uat, natlevname, natcode, centroid_geom, judet).

Extrage lat/lon din centroid_geom (ST_Y / ST_X) și precalculează coloane normalizate
(fără diacritice, majuscule) pentru căutare, la fel ca localitati/denumire din SIRUTA.

Necesită variabila de mediu LOCALITIES_DATABASE_URL (ex:
"postgresql+psycopg://user:pass@host:5432/gis_db"). Dacă nu este setată sau baza nu este
accesibilă, apelantul (init_db.py) trebuie să trateze excepția și să continue.

Rulează independent față de celelalte scripturi init_*; toate scriu în același SQLITE_DB.
"""
import os
import re
import sqlite3
import unicodedata

SQLITE_DB = os.getenv("SQLITE_DB", "caen.db")
LOCALITIES_DATABASE_URL = os.getenv("LOCALITIES_DATABASE_URL")


def _strip_diacritics(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )


def _normalize(name: str) -> str:
    return re.sub(r" {2,}", " ", name).strip()


def _norm_search(name: str) -> str:
    return _strip_diacritics(_normalize(name)).upper()


def init_localitati_geo_db() -> None:
    if not LOCALITIES_DATABASE_URL:
        raise RuntimeError("LOCALITIES_DATABASE_URL nu este setata.")

    import psycopg

    sqlite_conn = sqlite3.connect(SQLITE_DB)
    sqlite_conn.executescript("""
        DROP TABLE IF EXISTS localitati_geo;

        CREATE TABLE localitati_geo (
            gid           INTEGER PRIMARY KEY,
            nume_uat      TEXT NOT NULL,
            nume_uat_norm TEXT NOT NULL,
            natlevname    TEXT,
            natcode       TEXT,
            judet         TEXT NOT NULL,
            judet_norm    TEXT NOT NULL,
            lat           REAL,
            lon           REAL
        );

        CREATE INDEX idx_localitati_geo_nume_norm  ON localitati_geo(nume_uat_norm);
        CREATE INDEX idx_localitati_geo_judet_norm ON localitati_geo(judet_norm);
    """)

    with psycopg.connect(LOCALITIES_DATABASE_URL) as pg_conn:
        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT gid, nume_uat, natlevname, natcode, judet,
                       ST_Y(centroid_geom) AS lat, ST_X(centroid_geom) AS lon
                FROM public.localities
            """)
            rows = cur.fetchall()

    for gid, nume_uat, natlevname, natcode, judet, lat, lon in rows:
        nume_uat = _normalize(nume_uat)
        judet = _normalize(judet)
        sqlite_conn.execute(
            """
            INSERT OR IGNORE INTO localitati_geo
                (gid, nume_uat, nume_uat_norm, natlevname, natcode, judet, judet_norm, lat, lon)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (gid, nume_uat, _norm_search(nume_uat), natlevname, natcode, judet, _norm_search(judet), lat, lon),
        )

    sqlite_conn.commit()
    count = sqlite_conn.execute("SELECT COUNT(*) FROM localitati_geo").fetchone()[0]
    sqlite_conn.close()
    print(f"Localitati geo incarcate: {count} in '{SQLITE_DB}'")


if __name__ == "__main__":
    init_localitati_geo_db()
