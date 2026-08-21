"""Tests for scripts/import_company_caen.py.

Uses a throwaway SQLite DB (DATABASE_URL is read lazily by
routers.company_database, same as tests/test_companies_api.py) rather than the
shared Postgres fixture, since these tests don't need trigram search or any
other Postgres-only feature -- just the upsert logic itself.
"""
from pathlib import Path

import pytest
from sqlalchemy import select

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company, CompanyCaenCode
from routers.company_utils import normalize_company_name
from scripts.import_company_caen import (
    _iter_rows,
    _normalize_caen_version,
    _read_batches,
    _upsert_batch,
    import_company_caen,
)


@pytest.fixture
def caen_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "caen_import.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    init_postgres()


def _make_company(session, *, cui: int, registration_number: str, name: str = "Test SRL") -> Company:
    company = Company(
        name=name,
        normalized_name=normalize_company_name(name),
        cui=cui,
        registration_number=registration_number,
    )
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def _write_csv(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "caen_autorizat.csv"
    content = "COD_INMATRICULARE^COD_CAEN_AUTORIZAT^VER_CAEN_AUTORIZAT\n" + "\n".join(rows) + "\n"
    path.write_text(content, encoding="utf-8")
    return path


def test_normalize_caen_version_maps_numeric_codes() -> None:
    assert _normalize_caen_version("0") == "Versiunea 1998"
    assert _normalize_caen_version("1") == "Versiunea 2003"
    assert _normalize_caen_version("2") == "Versiunea 2008"
    assert _normalize_caen_version("3") == "Versiunea 2025"


def test_normalize_caen_version_passes_through_already_text_values() -> None:
    # Older ONRC exports spell the version out already (e.g. "Versiunea 2008");
    # newer ones use the bare nomenclature code. Either shape must round-trip.
    assert _normalize_caen_version("Versiunea 2008") == "Versiunea 2008"


def test_normalize_caen_version_none_stays_none() -> None:
    assert _normalize_caen_version(None) is None


def test_iter_rows_parses_and_normalizes_version(tmp_path: Path) -> None:
    csv_path = _write_csv(tmp_path, ["J40/1/2000^6201^2", "J40/2/2000^6202^3"])

    rows = list(_iter_rows(csv_path))

    assert rows == [
        ("J40/1/2000", "6201", "Versiunea 2008"),
        ("J40/2/2000", "6202", "Versiunea 2025"),
    ]


def test_iter_rows_flags_missing_registration_number_or_code_as_invalid(tmp_path: Path) -> None:
    csv_path = _write_csv(tmp_path, ["^6201^2", "J40/1/2000^^2"])

    rows = list(_iter_rows(csv_path))

    assert rows == [(None, None, None), (None, None, None)]


def test_read_batches_splits_on_batch_size(tmp_path: Path) -> None:
    csv_path = _write_csv(tmp_path, [f"J40/{i}/2000^6201^2" for i in range(5)])

    batches = list(_read_batches(csv_path, batch_size=2))

    assert [len(batch) for batch, _ in batches] == [2, 2, 1]
    assert all(invalid_rows == 0 for _, invalid_rows in batches)


def test_read_batches_counts_invalid_rows_separately_from_batch(tmp_path: Path) -> None:
    csv_path = _write_csv(tmp_path, ["^6201^2", "J40/1/2000^6202^2"])

    batches = list(_read_batches(csv_path, batch_size=10))

    assert len(batches) == 1
    batch, invalid_rows = batches[0]
    assert len(batch) == 1
    assert invalid_rows == 1


def test_upsert_batch_skips_unknown_registration_number(caen_db: None) -> None:
    with SessionLocal() as session:
        _make_company(session, cui=1, registration_number="J40/1/2000")

    batch = [
        ("J40/1/2000", "6201", "Versiunea 2008"),
        ("J40/999/2000", "6202", "Versiunea 2008"),
    ]
    stats = _upsert_batch(batch, invalid_rows=0)

    assert stats.skipped_no_company == 1
    assert stats.inserted == 1
    with SessionLocal() as session:
        rows = session.scalars(select(CompanyCaenCode)).all()
        assert len(rows) == 1
        assert rows[0].caen_code == "6201"


def test_upsert_batch_never_marks_a_code_principal(caen_db: None) -> None:
    # ONRC's bulk export doesn't mark which code is the registered principal
    # activity anywhere -- is_principal must stay False for everything imported
    # from this source (see the docstring on _iter_rows for the full story).
    with SessionLocal() as session:
        _make_company(session, cui=1, registration_number="J40/1/2000")

    stats = _upsert_batch([("J40/1/2000", "6201", "Versiunea 2008")], invalid_rows=0)

    assert stats.inserted == 1
    with SessionLocal() as session:
        row = session.scalar(select(CompanyCaenCode))
        assert row.is_principal is False


def test_upsert_batch_dedupes_same_company_and_code_within_one_batch(caen_db: None) -> None:
    # Regression test: a registration number's rows aren't always contiguous in
    # the source file, so the same (company, caen_code) pair can appear twice in
    # one batch. Before this was handled, that produced a Postgres
    # CardinalityViolation ("ON CONFLICT DO UPDATE command cannot affect row a
    # second time") and aborted the whole batch.
    with SessionLocal() as session:
        _make_company(session, cui=1, registration_number="J40/1/2000")

    batch = [
        ("J40/1/2000", "6201", "Versiunea 2008"),
        ("J40/1/2000", "5819", "Versiunea 2008"),
        ("J40/1/2000", "6201", "Versiunea 2008"),  # duplicate, non-contiguous in the source file
    ]
    stats = _upsert_batch(batch, invalid_rows=0)

    assert stats.inserted == 2
    assert stats.updated == 0
    with SessionLocal() as session:
        rows = session.scalars(select(CompanyCaenCode)).all()
        assert {row.caen_code for row in rows} == {"6201", "5819"}


def test_upsert_batch_updates_existing_row_on_rerun(caen_db: None) -> None:
    with SessionLocal() as session:
        _make_company(session, cui=1, registration_number="J40/1/2000")

    _upsert_batch([("J40/1/2000", "6201", "Versiunea 2008")], invalid_rows=0)
    stats = _upsert_batch([("J40/1/2000", "6201", "Versiunea 2025")], invalid_rows=0)

    assert stats.inserted == 0
    assert stats.updated == 1
    with SessionLocal() as session:
        row = session.scalar(select(CompanyCaenCode))
        assert row.caen_version == "Versiunea 2025"


def test_import_company_caen_end_to_end(tmp_path: Path, caen_db: None) -> None:
    with SessionLocal() as session:
        _make_company(session, cui=1, registration_number="J12/5638/2021", name="Mapnology SRL")
        _make_company(session, cui=2, registration_number="J40/2/2000", name="Other SRL")

    csv_path = _write_csv(
        tmp_path,
        [
            "J12/5638/2021^1812^2",
            "J12/5638/2021^5819^2",
            "J12/5638/2021^6201^2",
            "J40/2/2000^6202^3",
            "J99/999/2099^6203^2",  # unknown registration number
            "^6204^2",  # invalid row (missing registration number)
        ],
    )

    stats = import_company_caen(csv_path, batch_size=10)

    assert stats.rows_seen == 6
    assert stats.errors == 1
    assert stats.skipped_no_company == 1
    assert stats.inserted == 4

    with SessionLocal() as session:
        rows = session.scalars(select(CompanyCaenCode)).all()
        assert len(rows) == 4
        assert all(row.is_principal is False for row in rows)
        codes_by_company_id = {}
        for row in rows:
            codes_by_company_id.setdefault(row.company_id, set()).add(row.caen_code)
        assert len(codes_by_company_id) == 2
        assert {"1812", "5819", "6201"} in codes_by_company_id.values()
        assert {"6202"} in codes_by_company_id.values()


def test_import_company_caen_truncate_clears_existing_rows(tmp_path: Path, caen_db: None) -> None:
    with SessionLocal() as session:
        company = _make_company(session, cui=1, registration_number="J40/1/2000")
        session.add(
            CompanyCaenCode(
                company_id=company.id, caen_code="0000", is_principal=True, caen_version="stale"
            )
        )
        session.commit()

    csv_path = _write_csv(tmp_path, ["J40/1/2000^6201^2"])
    import_company_caen(csv_path, batch_size=10, truncate=True)

    with SessionLocal() as session:
        rows = session.scalars(select(CompanyCaenCode)).all()
        assert len(rows) == 1
        assert rows[0].caen_code == "6201"
