"""Tests for scripts/derive_company_closure_window.py.

Same throwaway-SQLite pattern as tests/test_import_company_caen.py for the DB-integration
parts; pure functions (date parsing, chronological fold) are tested without a DB.
"""
from datetime import date
from pathlib import Path

import pytest

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company
from routers.company_utils import normalize_company_name
from scripts.derive_company_closure_window import (
    ClosureResult,
    _CuiState,
    _finalize,
    _is_snapshot_file,
    build_closure_windows,
    discover_snapshots,
    snapshot_date,
    update_closure_windows,
)


class TestSnapshotDate:
    def test_dot_format_in_filename(self) -> None:
        assert snapshot_date(Path("2radiatefarasediu-31.07.2015.csv")) == date(2015, 7, 31)

    def test_dash_dmy_format_in_filename(self) -> None:
        assert snapshot_date(Path("2firme_radiate_fara_sediu_18-03-2025.csv")) == date(2025, 3, 18)

    def test_dash_ymd_format_in_filename(self) -> None:
        assert snapshot_date(Path("2firme_radiate_fara_sediu_2023-04-07.csv")) == date(2023, 4, 7)

    def test_falls_back_to_folder_name_ro_month(self, tmp_path: Path) -> None:
        folder = tmp_path / "firme-inregistrate-la-registrul-comertului-pana-la-data-de-07-aprilie-2022"
        folder.mkdir()
        path = folder / "2firme_radiate_fara_sediu.csv"
        assert snapshot_date(path) == date(2022, 4, 7)

    def test_no_date_found_returns_none(self, tmp_path: Path) -> None:
        folder = tmp_path / "no-date-here"
        folder.mkdir()
        assert snapshot_date(folder / "2firme_radiate_fara_sediu.csv") is None


class TestIsSnapshotFile:
    def test_recognizes_all_four_types(self) -> None:
        for name in (
            "1firme_neradiate_fara_sediu_18-03-2025.csv",
            "2radiatefarasediu-31.07.2015.csv",
            "3opendata-neradiatecusediu-01.02.2019.001.csv",
            "4open_data-radiate_cu_sediu-08.12.2020.csv",
        ):
            assert _is_snapshot_file(Path(name)) is True

    def test_excludes_nomenclator_and_current_state_files(self) -> None:
        for name in ("5nomenclator_stari_firma.csv", "n_stare_firma.csv", "od_stare_firma.csv", "od_firme.csv"):
            assert _is_snapshot_file(Path(name)) is False


class TestFinalize:
    def test_bracketed_window(self) -> None:
        state = _CuiState(last_active=date(2020, 1, 1), first_inactive_after=date(2020, 6, 1))
        result = _finalize(state)
        assert result == ClosureResult(date(2020, 1, 1), date(2020, 6, 1), "incadrata")

    def test_never_seen_active_is_left_censored(self) -> None:
        state = _CuiState(earliest_inactive_no_active=date(2015, 7, 31))
        result = _finalize(state)
        assert result == ClosureResult(None, date(2015, 7, 31), "necunoscuta_inainte_de_2015")

    def test_always_active_yields_nothing(self) -> None:
        state = _CuiState(last_active=date(2020, 1, 1))
        assert _finalize(state) is None

    def test_untouched_state_yields_nothing(self) -> None:
        assert _finalize(_CuiState()) is None


def _write_snapshot(path: Path, delimiter: str, header_cols: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [delimiter.join(header_cols)] + [delimiter.join(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestBuildClosureWindows:
    def test_active_then_inactive_brackets_window(self, tmp_path: Path) -> None:
        old = tmp_path / "3neradiatecusediu-31.07.2015.csv"
        _write_snapshot(
            old, "|", ["DENUMIRE", "CUI", "COD_INMATRICULARE", "STARE_FIRMA", "JUDET"],
            [["ACTIVA SRL", "111", "J1/1/2015", "1048", "Cluj"]],
        )
        new = tmp_path / "4firme_radiate_cu_sediu_18-03-2025.csv"
        _write_snapshot(
            new, "^", ["DENUMIRE", "CUI", "COD_INMATRICULARE", "EUID", "STARE_FIRMA"],
            [["ACTIVA SRL", "111", "J1/1/2015", "ROONRC.J1/1/2015", "1084"]],
        )

        snapshots = [(date(2015, 7, 31), old), (date(2025, 3, 18), new)]
        results = build_closure_windows(snapshots)

        assert results[111] == ClosureResult(date(2015, 7, 31), date(2025, 3, 18), "incadrata")

    def test_never_active_in_history_is_left_censored(self, tmp_path: Path) -> None:
        only = tmp_path / "4firme_radiate_cu_sediu_18-03-2025.csv"
        _write_snapshot(
            only, "^", ["DENUMIRE", "CUI", "COD_INMATRICULARE", "EUID", "STARE_FIRMA"],
            [["VECHE SRL", "333", "J1/3/2015", "ROONRC.J1/3/2015", "1048,1084"]],  # cod multiplu
        )

        results = build_closure_windows([(date(2025, 3, 18), only)])

        assert results[333] == ClosureResult(None, date(2025, 3, 18), "necunoscuta_inainte_de_2015")

    def test_always_active_never_appears_in_results(self, tmp_path: Path) -> None:
        path = tmp_path / "3neradiatecusediu-31.07.2015.csv"
        _write_snapshot(
            path, "|", ["DENUMIRE", "CUI", "COD_INMATRICULARE", "STARE_FIRMA", "JUDET"],
            [["ACTIVA SRL", "222", "J1/2/2015", "1048", "Cluj"]],
        )

        results = build_closure_windows([(date(2015, 7, 31), path)])

        assert 222 not in results

    def test_reactivation_resets_the_window_to_the_latest_transition(self, tmp_path: Path) -> None:
        s1 = tmp_path / "s1" / "3neradiatecusediu-31.07.2015.csv"
        _write_snapshot(
            s1, "|", ["DENUMIRE", "CUI", "COD_INMATRICULARE", "STARE_FIRMA", "JUDET"],
            [["X SRL", "444", "J1/4/2015", "1048", "Cluj"]],
        )
        s2 = tmp_path / "s2" / "4firme_radiate_cu_sediu_2018.csv"
        _write_snapshot(
            s2, "^", ["DENUMIRE", "CUI", "COD_INMATRICULARE", "EUID", "STARE_FIRMA"],
            [["X SRL", "444", "J1/4/2015", "ROONRC.J1/4/2015", "1052"]],
        )
        s3 = tmp_path / "s3" / "3neradiatecusediu-2020.csv"
        _write_snapshot(
            s3, "^", ["DENUMIRE", "CUI", "COD_INMATRICULARE", "EUID", "STARE_FIRMA"],
            [["X SRL", "444", "J1/4/2015", "ROONRC.J1/4/2015", "1048"]],
        )
        s4 = tmp_path / "s4" / "4firme_radiate_cu_sediu_2022.csv"
        _write_snapshot(
            s4, "^", ["DENUMIRE", "CUI", "COD_INMATRICULARE", "EUID", "STARE_FIRMA"],
            [["X SRL", "444", "J1/4/2015", "ROONRC.J1/4/2015", "1084"]],
        )

        snapshots = [
            (date(2015, 7, 31), s1),
            (date(2018, 1, 1), s2),
            (date(2020, 1, 1), s3),
            (date(2022, 1, 1), s4),
        ]
        results = build_closure_windows(snapshots)

        # fereastra relevanta e ultima tranzitie (2020 activ -> 2022 inactiv), nu prima (2015->2018)
        assert results[444] == ClosureResult(date(2020, 1, 1), date(2022, 1, 1), "incadrata")

    def test_invalid_cui_rows_are_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "4firme_radiate_cu_sediu_2025.csv"
        _write_snapshot(
            path, "^", ["DENUMIRE", "CUI", "COD_INMATRICULARE", "EUID", "STARE_FIRMA"],
            [
                ["PFA X", "0", "F1/1/2020", "ROONRC.F1/1/2020", "1084"],
                ["Y SRL", "555", "J1/5/2020", "ROONRC.J1/5/2020", "1084"],
            ],
        )

        results = build_closure_windows([(date(2025, 1, 1), path)])

        assert 0 not in results
        assert 555 in results


@pytest.fixture
def closure_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "closure.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    init_postgres()


def _make_company(session, *, cui: int, is_active: bool | None, name: str = "Test SRL") -> Company:
    company = Company(
        name=name,
        normalized_name=normalize_company_name(name),
        cui=cui,
        is_active=is_active,
    )
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


class TestUpdateClosureWindows:
    def test_only_updates_companies_already_marked_inactive(self, closure_db) -> None:
        with SessionLocal() as session:
            inactiva = _make_company(session, cui=111, is_active=False)
            activa = _make_company(session, cui=222, is_active=True)  # contradicts results on purpose

        results = {
            111: ClosureResult(date(2015, 7, 31), date(2025, 3, 18), "incadrata"),
            222: ClosureResult(date(2015, 7, 31), date(2025, 3, 18), "incadrata"),
        }

        stats = update_closure_windows(results, dry_run=False)

        assert stats.updated == 1
        with SessionLocal() as session:
            refreshed_inactiva = session.get(Company, inactiva.id)
            refreshed_activa = session.get(Company, activa.id)
            assert refreshed_inactiva.ultima_data_activa_cunoscuta == date(2015, 7, 31)
            assert refreshed_inactiva.prima_data_inactiva_cunoscuta == date(2025, 3, 18)
            assert refreshed_inactiva.fereastra_inchidere_tip == "incadrata"
            # firma activa in DB nu e atinsa, desi apare in `results`
            assert refreshed_activa.ultima_data_activa_cunoscuta is None
            assert refreshed_activa.fereastra_inchidere_tip is None

    def test_dry_run_does_not_write(self, closure_db) -> None:
        with SessionLocal() as session:
            company = _make_company(session, cui=333, is_active=False)

        results = {333: ClosureResult(None, date(2025, 3, 18), "necunoscuta_inainte_de_2015")}
        update_closure_windows(results, dry_run=True)

        with SessionLocal() as session:
            refreshed = session.get(Company, company.id)
            assert refreshed.fereastra_inchidere_tip is None
