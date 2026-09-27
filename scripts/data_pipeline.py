from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.data_sources import ensure_provenance_tables, record_dataset_run

PIPELINE_STEPS: list[dict[str, Any]] = [
    {
        "dataset": "caen",
        "module": "scripts.init_caen_db",
        "function": "init_db",
        "source": "CAEN Rev. 3",
        "source_url": "https://ec.europa.eu/eurostat/ramon/documents/codelists/codelist_caen_en.pdf",
        "source_dataset": "caen_rev3_coduri_clase.csv",
        "reference_period": "2025",
        "source_license": "public-domain",
    },
    {
        "dataset": "siruta",
        "module": "scripts.init_siruta_db",
        "function": "init_siruta",
        "source": "Institutul Național de Statistică — SIRUTA",
        "source_url": "https://insse.ro/cms/",
        "source_dataset": "siruta_cu_diacritice.csv; SIRUTA_an_2025/SIRUTA.csv; JUDET.DBF",
        "reference_period": "2025",
        "source_license": "government-open-data",
    },
    {
        "dataset": "exchange_rates",
        "module": "scripts.init_exchange_db",
        "function": "init_exchange_db",
        "source": "Banca Națională a României",
        "source_url": "https://www.bnr.ro",
        "source_dataset": "nbrfxrates{year}.xml",
        "reference_period": "2005-2026",
        "source_license": "public-domain",
    },
    {
        "dataset": "public_holidays",
        "module": "scripts.init_zile_libere_db",
        "function": "init_zile_libere_db",
        "source": "Autoritatea Națională pentru Muncă / legislație românească",
        "source_url": "https://legislatie.just.ro",
        "source_dataset": "zile_libere_legale_romania_2026.csv",
        "reference_period": "2026",
        "source_license": "government-open-data",
    },
    {
        "dataset": "postal_codes",
        "module": "scripts.init_coduri_postale_db",
        "function": "init_coduri_postale",
        "source": "Poșta Română",
        "source_url": "https://data.gov.ro/dataset/coduri-postale-romania",
        "source_dataset": "infocod-mai-2016_*.csv",
        "reference_period": "2016",
        "source_license": "open-data",
    },
    {
        "dataset": "localitati_geo",
        "module": "scripts.init_localitati_geo_db",
        "function": "init_localitati_geo_db",
        "source": "Domeniul public / geo-data localități",
        "source_url": "https://geoportal.ancpi.ro/",
        "source_dataset": "public.localities",
        "reference_period": "2025",
        "source_license": "government-open-data",
    },
    {
        "dataset": "companies",
        "module": "scripts.import_companies",
        "function": "import_companies",
        "source": "OnRC / Registrul Comerțului",
        "source_url": "https://data.gov.ro/dataset/registrul-comertului",
        "source_dataset": "ONRC bulk export",
        "reference_period": "live",
        "source_license": "open-data",
    },
]


def get_pipeline_steps() -> list[dict[str, Any]]:
    return [step.copy() for step in PIPELINE_STEPS]


def build_pipeline_manifest() -> dict[str, Any]:
    return {
        "pipeline_version": "v2026.09.23",
        "steps": get_pipeline_steps(),
        "root": str(Path(__file__).resolve().parent.parent),
    }


def _count_rows(db_path: str | Path, dataset: str) -> int:
    if dataset == "companies":
        return 0

    counts = {
        "caen": "SELECT COUNT(*) FROM clase",
        "siruta": "SELECT COUNT(*) FROM localitati",
        "exchange_rates": "SELECT COUNT(*) FROM cursuri_valutare",
        "public_holidays": "SELECT COUNT(*) FROM zile_libere",
        "postal_codes": "SELECT COUNT(*) FROM coduri_postale",
        "localitati_geo": "SELECT COUNT(*) FROM localitati_geo",
    }
    sql = counts.get(dataset)
    if sql is None:
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


def _execute_step(step: dict[str, Any], db_path: str | Path) -> int:
    dataset_name = step["dataset"]
    if dataset_name == "caen":
        from scripts.init_caen_db import init_db
        result = init_db(db_path=db_path)
    elif dataset_name == "siruta":
        from scripts.init_siruta_db import init_siruta, init_siruta_extins
        init_siruta(db_path=db_path)
        init_siruta_extins(db_path=db_path)
        result = _count_rows(db_path, dataset_name)
    elif dataset_name == "exchange_rates":
        from scripts.init_exchange_db import init_exchange_db
        result = init_exchange_db(db_path=db_path)
    elif dataset_name == "public_holidays":
        from scripts.init_zile_libere_db import init_zile_libere_db
        result = init_zile_libere_db(db_path=db_path)
    elif dataset_name == "postal_codes":
        from scripts.init_coduri_postale_db import init_coduri_postale
        result = init_coduri_postale(db_path=db_path)
    elif dataset_name == "localitati_geo":
        from scripts.init_localitati_geo_db import init_localitati_geo_db
        result = init_localitati_geo_db(db_path=db_path)
    elif dataset_name == "companies":
        from scripts.import_companies import import_companies
        source_file = step.get("source_file")
        if source_file is None:
            source_file = Path(__file__).resolve().parent.parent / "temp" / "onrc" / "od_firme.csv"
        result = import_companies(Path(source_file), truncate=False)
        return int(getattr(result, "inserted", 0) or getattr(result, "processed", 0) or 0)
    else:
        raise ValueError(f"Unsupported dataset: {dataset_name}")
    return int(result or _count_rows(db_path, dataset_name))


def run_pipeline(db_path: str | Path | None = None, *, pipeline_version: str | None = None) -> list[dict[str, Any]]:
    target_db = str(db_path or Path(__file__).resolve().parent.parent / "caen.db")
    manifest = build_pipeline_manifest()
    resolved_version = pipeline_version or manifest["pipeline_version"]
    ensure_provenance_tables(target_db)
    results: list[dict[str, Any]] = []

    for step in PIPELINE_STEPS:
        dataset_name = step["dataset"]
        processed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            rows_imported = _execute_step(step, target_db)
            record_dataset_run(
                db_path=target_db,
                dataset_name=dataset_name,
                source=step["source"],
                source_url=step.get("source_url"),
                source_dataset=step.get("source_dataset"),
                download_date=step.get("download_date"),
                reference_period=step.get("reference_period"),
                source_license=step.get("source_license"),
                pipeline_version=resolved_version,
                processed_at=processed_at,
                rows_imported=rows_imported,
            )
            results.append({"dataset": dataset_name, "status": "success", "rows_imported": rows_imported})
        except Exception as exc:  # pragma: no cover - safety guard for orchestration
            record_dataset_run(
                db_path=target_db,
                dataset_name=dataset_name,
                source=step["source"],
                source_url=step.get("source_url"),
                source_dataset=step.get("source_dataset"),
                download_date=step.get("download_date"),
                reference_period=step.get("reference_period"),
                source_license=step.get("source_license"),
                pipeline_version=resolved_version,
                processed_at=processed_at,
                rows_imported=0,
                status="failed",
                notes=str(exc),
            )
            raise
    return results
