from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "caen.db"


def _resolve_db_path(db_path: str | Path | None = None) -> Path:
    if db_path is None:
        return DEFAULT_DB_PATH
    return Path(db_path)


def ensure_provenance_tables(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Create the provenance metadata table used to track dataset sources."""
    path = _resolve_db_path(db_path)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dataset_provenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset TEXT NOT NULL,
            source TEXT NOT NULL,
            source_url TEXT,
            source_dataset TEXT,
            download_date TEXT,
            reference_period TEXT,
            source_license TEXT,
            pipeline_version TEXT,
            processed_at TEXT,
            rows_imported INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'success',
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dataset_provenance_dataset ON dataset_provenance(dataset)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dataset_provenance_processed_at ON dataset_provenance(processed_at)"
    )
    conn.commit()
    return conn


def record_dataset_run(
    *,
    dataset_name: str,
    source: str,
    source_url: str | None = None,
    source_dataset: str | None = None,
    download_date: str | None = None,
    reference_period: str | None = None,
    source_license: str | None = None,
    pipeline_version: str | None = None,
    processed_at: str | None = None,
    rows_imported: int = 0,
    status: str = "success",
    notes: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    conn = ensure_provenance_tables(db_path)
    processed = processed_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn.execute(
        """
        INSERT INTO dataset_provenance (
            dataset,
            source,
            source_url,
            source_dataset,
            download_date,
            reference_period,
            source_license,
            pipeline_version,
            processed_at,
            rows_imported,
            status,
            notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            dataset_name,
            source,
            source_url,
            source_dataset,
            download_date,
            reference_period,
            source_license,
            pipeline_version,
            processed,
            rows_imported,
            status,
            notes,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM dataset_provenance WHERE dataset = ? ORDER BY id DESC LIMIT 1",
        (dataset_name,),
    ).fetchone()
    columns = [col[1] for col in conn.execute("PRAGMA table_info(dataset_provenance)").fetchall()]
    conn.close()
    return dict(zip(columns, row)) if row else {}


def get_dataset_metadata(
    db_path: str | Path | None = None,
    dataset_name: str | None = None,
) -> dict[str, Any]:
    path = _resolve_db_path(db_path)
    conn = sqlite3.connect(str(path))
    if dataset_name is None:
        row = conn.execute(
            """
            SELECT *
            FROM dataset_provenance
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT *
            FROM dataset_provenance
            WHERE dataset = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (dataset_name,),
        ).fetchone()
    columns = [col[1] for col in conn.execute("PRAGMA table_info(dataset_provenance)").fetchall()]
    conn.close()
    if row is None:
        return {}
    return dict(zip(columns, row))
