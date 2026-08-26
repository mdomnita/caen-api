"""Tests for scripts/update_company_caen_principal.py.

Same throwaway-SQLite pattern as tests/test_import_company_caen.py. requests.post is
monkeypatched -- no real call to ANAF.
"""
from pathlib import Path

import pytest
from sqlalchemy import select

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company, CompanyCaenCode
from routers.company_utils import normalize_company_name
from scripts.update_company_caen_principal import (
    _normalize_caen_code,
    update_company_caen_principal,
)


@pytest.fixture
def caen_principal_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "caen_principal.db"
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


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


def _patch_anaf(monkeypatch: pytest.MonkeyPatch, response_by_call: list[dict]):
    """response_by_call[i] is returned for the i-th call to requests.post."""
    calls = []

    def fake_post(url, json, headers, proxies, timeout):
        calls.append({"url": url, "json": json, "proxies": proxies})
        return _FakeResponse(response_by_call[len(calls) - 1])

    monkeypatch.setattr("scripts.update_company_caen_principal.requests.post", fake_post)
    return calls


class TestNormalizeCaenCode:
    def test_zero_pads_short_codes(self) -> None:
        assert _normalize_caen_code(142) == "0142"
        assert _normalize_caen_code("142") == "0142"

    def test_leaves_four_digit_codes_unchanged(self) -> None:
        assert _normalize_caen_code("6201") == "6201"

    def test_none_becomes_none(self) -> None:
        assert _normalize_caen_code(None) is None


class TestUpdateCompanyCaenPrincipal:
    def test_marks_matching_code_as_principal(self, caen_principal_db, monkeypatch: pytest.MonkeyPatch) -> None:
        with SessionLocal() as session:
            company = _make_company(session, cui=111, registration_number="J1/1/2020")
            session.add_all(
                [
                    CompanyCaenCode(company_id=company.id, caen_code="6201", is_principal=False),
                    CompanyCaenCode(company_id=company.id, caen_code="4791", is_principal=True),  # stale, must flip to False
                ]
            )
            session.commit()

        _patch_anaf(
            monkeypatch,
            [{"found": [{"date_generale": {"cui": 111, "cod_CAEN": "6201"}}], "notFound": []}],
        )

        stats = update_company_caen_principal(sleep=0, use_proxy=False)

        assert stats.ok == 1
        assert stats.processed == 1
        with SessionLocal() as session:
            rows = {
                row.caen_code: row.is_principal
                for row in session.scalars(
                    select(CompanyCaenCode).where(CompanyCaenCode.company_id == company.id)
                )
            }
            assert rows == {"6201": True, "4791": False}

            refreshed = session.get(Company, company.id)
            assert refreshed.caen_principal_status == "ok"
            assert refreshed.caen_principal_verificat_la is not None

    def test_missing_code_is_reported_but_nothing_inserted(self, caen_principal_db, monkeypatch: pytest.MonkeyPatch) -> None:
        with SessionLocal() as session:
            company = _make_company(session, cui=222, registration_number="J1/2/2020")
            session.add(CompanyCaenCode(company_id=company.id, caen_code="4791", is_principal=False))
            session.commit()

        _patch_anaf(
            monkeypatch,
            [{"found": [{"date_generale": {"cui": 222, "cod_CAEN": "6201"}}], "notFound": []}],
        )

        stats = update_company_caen_principal(sleep=0, use_proxy=False)

        assert stats.cod_lipsa == 1
        with SessionLocal() as session:
            rows = list(
                session.scalars(select(CompanyCaenCode).where(CompanyCaenCode.company_id == company.id))
            )
            assert len(rows) == 1  # nothing inserted
            assert rows[0].is_principal is False  # untouched

            refreshed = session.get(Company, company.id)
            assert refreshed.caen_principal_status == "cod_lipsa"

    def test_not_found_at_anaf(self, caen_principal_db, monkeypatch: pytest.MonkeyPatch) -> None:
        with SessionLocal() as session:
            company = _make_company(session, cui=333, registration_number="J1/3/2020")

        _patch_anaf(monkeypatch, [{"found": [], "notFound": [333]}])

        stats = update_company_caen_principal(sleep=0, use_proxy=False)

        assert stats.not_found == 1
        with SessionLocal() as session:
            refreshed = session.get(Company, company.id)
            assert refreshed.caen_principal_status == "not_found"

    def test_request_error_leaves_status_null_for_retry(self, caen_principal_db, monkeypatch: pytest.MonkeyPatch) -> None:
        import requests

        with SessionLocal() as session:
            company = _make_company(session, cui=444, registration_number="J1/4/2020")

        def fake_post(*args, **kwargs):
            raise requests.RequestException("boom")

        monkeypatch.setattr("scripts.update_company_caen_principal.requests.post", fake_post)

        stats = update_company_caen_principal(sleep=0, use_proxy=False)

        assert stats.error == 1
        with SessionLocal() as session:
            refreshed = session.get(Company, company.id)
            assert refreshed.caen_principal_status is None  # left for automatic retry

    def test_default_run_skips_already_checked_companies(self, caen_principal_db, monkeypatch: pytest.MonkeyPatch) -> None:
        with SessionLocal() as session:
            company = _make_company(session, cui=555, registration_number="J1/5/2020")
            company.caen_principal_status = "not_found"
            session.commit()

        calls = _patch_anaf(monkeypatch, [{"found": [], "notFound": []}])

        stats = update_company_caen_principal(sleep=0, use_proxy=False)

        assert stats.processed == 0
        assert calls == []

    def test_retry_failed_reprocesses_not_found(self, caen_principal_db, monkeypatch: pytest.MonkeyPatch) -> None:
        with SessionLocal() as session:
            company = _make_company(session, cui=666, registration_number="J1/6/2020")
            session.add(CompanyCaenCode(company_id=company.id, caen_code="6201", is_principal=False))
            company.caen_principal_status = "not_found"
            session.commit()

        _patch_anaf(
            monkeypatch,
            [{"found": [{"date_generale": {"cui": 666, "cod_CAEN": "6201"}}], "notFound": []}],
        )

        stats = update_company_caen_principal(sleep=0, use_proxy=False, retry_failed=True)

        assert stats.ok == 1
        with SessionLocal() as session:
            refreshed = session.get(Company, company.id)
            assert refreshed.caen_principal_status == "ok"

    def test_dry_run_does_not_write(self, caen_principal_db, monkeypatch: pytest.MonkeyPatch) -> None:
        with SessionLocal() as session:
            company = _make_company(session, cui=777, registration_number="J1/7/2020")
            session.add(CompanyCaenCode(company_id=company.id, caen_code="6201", is_principal=False))
            session.commit()

        _patch_anaf(
            monkeypatch,
            [{"found": [{"date_generale": {"cui": 777, "cod_CAEN": "6201"}}], "notFound": []}],
        )

        update_company_caen_principal(sleep=0, use_proxy=False, dry_run=True)

        with SessionLocal() as session:
            refreshed = session.get(Company, company.id)
            assert refreshed.caen_principal_status is None
            row = session.scalar(select(CompanyCaenCode).where(CompanyCaenCode.company_id == company.id))
            assert row.is_principal is False
