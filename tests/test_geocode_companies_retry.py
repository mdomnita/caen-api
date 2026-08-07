"""Tests for the deadlock-retry logic in scripts/geocode_companies.py.

Uses mocking rather than a real Postgres deadlock: _process_batch is
monkeypatched to simulate failures, so these run fast and without a DB.
"""
from unittest.mock import patch

import pytest
from sqlalchemy.exc import OperationalError

from scripts.geocode_companies import GeocodeStats, geocode_companies


def _deadlock_error() -> OperationalError:
    return OperationalError("UPDATE companies ...", {}, Exception("deadlock detected"))


def _other_error() -> OperationalError:
    return OperationalError("UPDATE companies ...", {}, Exception("connection reset by peer"))


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("scripts.geocode_companies.time.sleep"):
        yield


@pytest.fixture(autouse=True)
def _no_init_postgres():
    with patch("scripts.geocode_companies.init_postgres"):
        yield


def test_retries_on_deadlock_then_succeeds():
    calls = []

    def fake_process_batch(fetch_size, last_id, retry_failed, dry_run, executor, provider, sleep):
        calls.append(last_id)
        if len(calls) == 1:
            raise _deadlock_error()
        if last_id == 0:
            return 5, GeocodeStats(processed=5, ok=5)
        return None  # no more rows

    with patch("scripts.geocode_companies._process_batch", side_effect=fake_process_batch):
        stats = geocode_companies(batch_size=5)

    # the failed attempt (last_id=0) and the retried attempt (last_id=0 again)
    # both used last_id=0 -- proving the same batch was re-fetched, not skipped
    assert calls == [0, 0, 5]
    assert stats.processed == 5
    assert stats.ok == 5


def test_gives_up_immediately_on_non_deadlock_error():
    def fake_process_batch(*args, **kwargs):
        raise _other_error()

    with patch("scripts.geocode_companies._process_batch", side_effect=fake_process_batch):
        with pytest.raises(OperationalError):
            geocode_companies(batch_size=5)


def test_raises_after_exhausting_retries():
    attempts = []

    def fake_process_batch(*args, **kwargs):
        attempts.append(1)
        raise _deadlock_error()

    with patch("scripts.geocode_companies._process_batch", side_effect=fake_process_batch):
        with pytest.raises(OperationalError):
            geocode_companies(batch_size=5)

    from scripts.geocode_companies import MAX_COMMIT_RETRIES
    assert len(attempts) == MAX_COMMIT_RETRIES + 1


def test_stats_not_double_counted_across_retry():
    calls = []

    def fake_process_batch(fetch_size, last_id, retry_failed, dry_run, executor, provider, sleep):
        calls.append(last_id)
        if len(calls) == 1:
            raise _deadlock_error()
        if last_id == 0:
            return 3, GeocodeStats(processed=3, ok=2, not_found=1)
        return None

    with patch("scripts.geocode_companies._process_batch", side_effect=fake_process_batch):
        stats = geocode_companies(batch_size=5)

    assert stats.processed == 3  # not 6 -- the failed attempt's local stats were discarded
    assert stats.ok == 2
    assert stats.not_found == 1
