from datetime import date
from pathlib import Path
from types import SimpleNamespace

from scripts.import_all_companies import (
    _merge_payload,
    _source_encoding,
    discover_company_sources,
    iter_source_rows,
)


def test_discovers_only_company_identity_exports_in_date_order(tmp_path: Path) -> None:
    old = tmp_path / "firme-pana-la-31-07-2015"
    old.mkdir()
    (old / "3neradiatecusediu-31.07.2015.csv").write_text(
        "DENUMIRE|CUI|COD_INMATRICULARE|STARE_FIRMA|JUDET|LOCALITATE\n",
        encoding="utf-8",
    )
    modern = tmp_path / "firme-13-11-2025"
    modern.mkdir()
    (modern / "od_firme.csv").write_text(
        "DENUMIRE^CUI^COD_INMATRICULARE^DATA_INMATRICULARE\n",
        encoding="utf-8",
    )
    (modern / "od_stare_firma.csv").write_text(
        "COD_INMATRICULARE^COD\n", encoding="utf-8"
    )

    sources = discover_company_sources(tmp_path)

    assert [(observed, path.name) for observed, path in sources] == [
        (date(2015, 7, 31), "3neradiatecusediu-31.07.2015.csv"),
        (date(2025, 11, 13), "od_firme.csv"),
    ]


def test_reads_headerless_legacy_continuation_chunk(tmp_path: Path) -> None:
    source = tmp_path / "3opendata-neradiatecusediu-30.04.2018.002.csv"
    source.write_text(
        "FIRMA TEST SRL^123^J01/1/2018^ROONRC.J01/1/2018^1048^Adresa completa\n",
        encoding="utf-8",
    )

    assert list(iter_source_rows(source)) == [
        {
            "DENUMIRE": "FIRMA TEST SRL",
            "CUI": "123",
            "COD_INMATRICULARE": "J01/1/2018",
            "EUID": "ROONRC.J01/1/2018",
            "STARE_FIRMA": "1048",
            "ADRESA": "Adresa completa",
        }
    ]


def test_mostly_utf8_file_with_one_bad_byte_is_not_misclassified_as_cp1250(
    tmp_path: Path,
) -> None:
    source = tmp_path / "snapshot.csv"
    content = (
        "DENUMIRE^CUI^COD_INMATRICULARE^JUDET\n"
        "FIRMA SRL^123^J40/1/2020^Bucureşti\n"
    ).encode("utf-8")
    source.write_bytes(content * 100 + b"\x81")

    assert _source_encoding(source) == "utf-8-sig"


def test_merge_preserves_rich_values_and_earliest_inactive_date() -> None:
    company = SimpleNamespace(
        name="Nume vechi",
        county="Cluj",
        locality="Cluj-Napoca",
        prima_data_inactiva_cunoscuta=date(2020, 1, 1),
    )

    changed, address_changed = _merge_payload(
        company,
        {
            "name": "Nume nou",
            "county": None,
            "locality": "Turda",
            "prima_data_inactiva_cunoscuta": date(2021, 1, 1),
        },
    )

    assert changed == {"name", "locality"}
    assert address_changed is True
    assert company.name == "Nume nou"
    assert company.county == "Cluj"
    assert company.prima_data_inactiva_cunoscuta == date(2020, 1, 1)
