from pathlib import Path

from scripts.data_pipeline import PIPELINE_STEPS
from scripts.data_sources import ensure_provenance_tables, get_dataset_metadata, record_dataset_run


def test_provenance_records_are_written_for_each_dataset(tmp_path: Path) -> None:
    db_path = tmp_path / "test_provenance.db"
    ensure_provenance_tables(db_path)

    record_dataset_run(
        db_path=db_path,
        dataset_name="caen",
        source="CAEN Rev. 3",
        source_url="https://example.invalid/caen.csv",
        source_dataset="caen_rev3_coduri_clase.csv",
        download_date="2026-09-23",
        reference_period="2025",
        source_license="public-domain",
        pipeline_version="v2026.09.23",
        processed_at="2026-09-23T12:00:00Z",
        rows_imported=42,
    )

    meta = get_dataset_metadata(db_path, "caen")
    assert meta["source"] == "CAEN Rev. 3"
    assert meta["source_url"] == "https://example.invalid/caen.csv"
    assert meta["source_dataset"] == "caen_rev3_coduri_clase.csv"
    assert meta["download_date"] == "2026-09-23"
    assert meta["reference_period"] == "2025"
    assert meta["source_license"] == "public-domain"
    assert meta["pipeline_version"] == "v2026.09.23"
    assert meta["processed_at"] == "2026-09-23T12:00:00Z"
    assert meta["rows_imported"] == 42


def test_pipeline_manifest_lists_all_data_sources_in_order() -> None:
    names = [step["dataset"] for step in PIPELINE_STEPS]

    assert names == [
        "caen",
        "siruta",
        "exchange_rates",
        "public_holidays",
        "postal_codes",
        "localitati_geo",
        "companies",
    ]
