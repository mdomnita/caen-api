"""Tests for scripts/match_localitati_geo_siruta.py.

Runs directly against the shared test SQLITE_DB (via the `client` fixture, which
ensures the app -- and SQLITE_DB -- are initialized) rather than a throwaway DB,
since the module-level fixture in conftest.py already seeds a realistic mix of
UAT/componenta/ambiguous/unmatched cases for this script to resolve.
"""
import os
import sqlite3

from scripts.match_localitati_geo_siruta import match_localitati_geo_siruta


def _reset_cod_siruta() -> None:
    conn = sqlite3.connect(os.environ["SQLITE_DB"])
    conn.execute("UPDATE localitati_geo SET cod_siruta = NULL")
    conn.commit()
    conn.close()


def test_resolves_uat_level_match(client) -> None:
    _reset_cod_siruta()
    match_localitati_geo_siruta()

    conn = sqlite3.connect(os.environ["SQLITE_DB"])
    cod_siruta = conn.execute("SELECT cod_siruta FROM localitati_geo WHERE gid = 1").fetchone()[0]
    conn.close()
    assert cod_siruta == 666  # Focsani -> localitati.cod_siruta=666


def test_leaves_unmatched_rows_null_not_guessed(client) -> None:
    _reset_cod_siruta()
    match_localitati_geo_siruta()

    # gid 2/3 ("Independenta", Constanta/Galati) have no localitati/localitati_componente
    # counterpart in the fixture -- must stay NULL, not be assigned a wrong code.
    conn = sqlite3.connect(os.environ["SQLITE_DB"])
    rows = conn.execute("SELECT gid, cod_siruta FROM localitati_geo WHERE gid IN (2, 3)").fetchall()
    conn.close()
    assert all(cod_siruta is None for _, cod_siruta in rows)


def test_returns_accurate_counts(client) -> None:
    _reset_cod_siruta()
    potrivite, ambigue, nepotrivite = match_localitati_geo_siruta()

    assert potrivite == 1  # only gid=1 (Focsani) matches
    assert nepotrivite == 2  # gid=2, gid=3
    assert ambigue == 0


def test_rerun_is_idempotent(client) -> None:
    _reset_cod_siruta()
    first = match_localitati_geo_siruta()
    second = match_localitati_geo_siruta()
    assert first == second
