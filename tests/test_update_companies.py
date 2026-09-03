from types import SimpleNamespace

import pytest

from scripts.update_companies import UpdateStats, _changed_fields, update_companies


def test_changed_fields_returns_only_different_values() -> None:
    company = SimpleNamespace(cui=123, name="Firma SRL", county="Cluj")

    assert _changed_fields(
        company,
        {"cui": 123, "name": "Firma Noua SRL", "county": "Cluj"},
    ) == {"name"}


def test_update_stats_merge_includes_new_counters() -> None:
    total = UpdateStats(updated=1, unchanged=2, geocodes_invalidated=1)

    total.merge(UpdateStats(updated=3, unchanged=4, geocodes_invalidated=2))

    assert total.updated == 4
    assert total.unchanged == 6
    assert total.geocodes_invalidated == 3


def test_update_companies_rejects_non_positive_batch_size(tmp_path) -> None:
    source = tmp_path / "od_firme.csv"
    source.write_text("DENUMIRE^CUI\n", encoding="utf-8")

    with pytest.raises(ValueError, match="mai mare decat zero"):
        update_companies(source, batch_size=0)


def test_update_companies_rejects_missing_source(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="Fisierul sursa nu exista"):
        update_companies(tmp_path / "missing.csv")
