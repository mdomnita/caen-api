"""
Convert caen.db to a SpatiaLite database (caen_spatial.db).

All regular tables are copied as-is.  For tables that have lat/lon columns
(currently: localitati_geo), a SpatiaLite POINT geometry column (EPSG 4326,
WGS 84) named 'geom' is added and populated from those columns, and a
SpatiaLite spatial index is created.

The SQLite R*Tree virtual tables (localitati_geo_rtree and its shadow tables)
are intentionally omitted — the SpatiaLite spatial index replaces them.

Requirements:
  - mod_spatialite shared library must be loadable (on PATH or in CWD).
    Windows: download from https://www.gaia-gis.it/gaia-sins/ or install via OSGeo4W.
    Linux/macOS: install libspatialite via package manager.

Usage:
    python scripts/convert_to_spatialite.py
    python scripts/convert_to_spatialite.py --src caen.db --dst caen_spatial.db
"""
import argparse
import os
import sqlite3
import sys

SRC_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "caen.db")
DST_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "caen_spatial.db")

# Virtual-table shadow tables and internal SQLite tables — not copied.
SKIP_TABLES = {
    "sqlite_sequence",
    "sqlite_stat1",
    "localitati_geo_rtree",
    "localitati_geo_rtree_rowid",
    "localitati_geo_rtree_node",
    "localitati_geo_rtree_parent",
}

# Tables that carry geographic coordinates: {table: (lon_col, lat_col)}.
# MakePoint(x, y) expects x=longitude, y=latitude.
GEO_TABLES: dict[str, tuple[str, str]] = {
    "localitati_geo": ("lon", "lat"),
}


_WINDOWS_SEARCH_DIRS = [
    r"C:\Program Files\QGIS 4.0.0\bin",
    r"C:\Program Files\QGIS 3.42.1\bin",
    r"C:\Program Files\ArcGIS\Pro\bin",
    r"C:\OSGeo4W\bin",
]


def _add_dll_dirs_windows() -> None:
    """Register candidate DLL directories so Windows can resolve dependencies."""
    if not hasattr(os, "add_dll_directory"):
        return
    for path in _WINDOWS_SEARCH_DIRS:
        if os.path.isdir(path):
            os.add_dll_directory(path)


def load_spatialite(conn: sqlite3.Connection) -> None:
    if sys.platform == "win32":
        _add_dll_dirs_windows()

    conn.enable_load_extension(True)
    for name in ("mod_spatialite", "spatialite"):
        try:
            conn.load_extension(name)
            conn.enable_load_extension(False)
            return
        except sqlite3.OperationalError:
            continue
    conn.enable_load_extension(False)
    sys.exit(
        "ERROR: could not load SpatiaLite extension (tried 'mod_spatialite' and 'spatialite').\n"
        "Windows : install QGIS (https://qgis.org) or download mod_spatialite.dll from\n"
        "          https://www.gaia-gis.it/gaia-sins/ and add its folder to _WINDOWS_SEARCH_DIRS.\n"
        "Linux   : sudo apt install libsqlite3-mod-spatialite   (or equivalent)\n"
        "macOS   : brew install spatialite-tools"
    )


def regular_tables(src: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = src.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY rowid"
    ).fetchall()
    return [
        (name, sql)
        for name, sql in rows
        if sql and name not in SKIP_TABLES and not name.startswith("sqlite_")
    ]


def regular_indexes(src: sqlite3.Connection, copied_tables: set[str]) -> list[str]:
    rows = src.execute(
        "SELECT tbl_name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
    ).fetchall()
    return [sql for tbl_name, sql in rows if tbl_name in copied_tables]


def copy_table(src: sqlite3.Connection, dst: sqlite3.Connection, name: str, ddl: str) -> int:
    dst.execute(ddl)
    cols = [c[1] for c in src.execute(f"PRAGMA table_info({name})").fetchall()]
    col_list = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join("?" * len(cols))
    rows = src.execute(f"SELECT {col_list} FROM \"{name}\"").fetchall()
    dst.executemany(
        f'INSERT INTO "{name}" ({col_list}) VALUES ({placeholders})',
        rows,
    )
    return len(rows)


def add_geometry(dst: sqlite3.Connection, table: str, lon_col: str, lat_col: str) -> int:
    dst.execute(
        "SELECT AddGeometryColumn(?, 'geom', 4326, 'POINT', 'XY')",
        (table,),
    )
    dst.commit()

    updated = dst.execute(
        f"""
        UPDATE "{table}"
        SET geom = MakePoint("{lon_col}", "{lat_col}", 4326)
        WHERE "{lon_col}" IS NOT NULL AND "{lat_col}" IS NOT NULL
        """
    ).rowcount
    dst.commit()

    # CreateSpatialIndex manages its own transaction internally.
    dst.execute("SELECT CreateSpatialIndex(?, 'geom')", (table,))
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", default=SRC_DEFAULT, help="Source SQLite database (default: caen.db)")
    parser.add_argument("--dst", default=DST_DEFAULT, help="Destination SpatiaLite database (default: caen_spatial.db)")
    args = parser.parse_args()

    src_path = os.path.abspath(args.src)
    dst_path = os.path.abspath(args.dst)

    if not os.path.exists(src_path):
        sys.exit(f"ERROR: source database not found: {src_path}")

    if os.path.exists(dst_path):
        os.remove(dst_path)
        print(f"Removed existing {dst_path}")

    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    dst.execute("PRAGMA journal_mode=WAL")

    print("Loading SpatiaLite…")
    load_spatialite(dst)

    print("Initialising SpatiaLite metadata…")
    # transaction=1 means InitSpatialMetaData handles its own commit.
    dst.execute("SELECT InitSpatialMetaData(1)")

    tables = regular_tables(src)
    copied_table_names = {name for name, _ in tables}

    print(f"\nCopying {len(tables)} tables…")
    dst.execute("BEGIN")
    for name, ddl in tables:
        count = copy_table(src, dst, name, ddl)
        print(f"  {name}: {count} rows")
    dst.execute("COMMIT")

    print("\nAdding geometry columns…")
    for table, (lon_col, lat_col) in GEO_TABLES.items():
        if table not in copied_table_names:
            print(f"  {table}: not found in source, skipped")
            continue
        updated = add_geometry(dst, table, lon_col, lat_col)
        print(f"  {table}: geom set for {updated} rows (lon={lon_col}, lat={lat_col}), spatial index created")

    indexes = regular_indexes(src, copied_table_names)
    print(f"\nCopying {len(indexes)} indexes…")
    dst.execute("BEGIN")
    for sql in indexes:
        try:
            dst.execute(sql)
        except sqlite3.OperationalError as exc:
            print(f"  WARNING: index skipped ({exc}): {sql[:80]}")
    dst.execute("COMMIT")

    src.close()
    dst.close()

    size_mb = os.path.getsize(dst_path) / 1_048_576
    print(f"\nDone. {dst_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
