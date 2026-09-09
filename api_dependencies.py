import sqlite3
from collections.abc import Generator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from auth import SQLITE_DB
from routers.company_database import get_session as get_company_session_dependency


SQLITE_SECTIONS = {
    "caen",
    "sectiuni",
    "diviziuni",
    "grupe",
    "siruta",
    "schimb",
    "zilelibere",
    "localitati",
    "coduripostale",
}
POSTGRES_SECTIONS = {"companii", "representatives"}
ALL_SECTIONS = SQLITE_SECTIONS | POSTGRES_SECTIONS


@dataclass
class ApiDatabaseContext:
    section: str
    sqlite_conn: sqlite3.Connection | None = None
    postgres_session: Session | None = None


def _get_api_section(request: Request) -> str:
    segments = [segment for segment in request.url.path.split("/") if segment]
    return next((segment for segment in segments if segment in ALL_SECTIONS), "")


def get_api_database_context(request: Request) -> Generator[ApiDatabaseContext, None, None]:
    section = _get_api_section(request)

    if section in POSTGRES_SECTIONS:
        session_generator = get_company_session_dependency()
        session = next(session_generator)
        try:
            yield ApiDatabaseContext(section=section, postgres_session=session)
        finally:
            try:
                next(session_generator)
            except StopIteration:
                pass
        return

    if section in SQLITE_SECTIONS:
        # check_same_thread=False: FastAPI's sync-dependency wrapper opens and
        # closes this connection via two separate threadpool calls that
        # aren't guaranteed to land on the same OS thread. That's fine here
        # (each connection is only ever used by one request at a time, never
        # concurrently) — we just need sqlite3 to not enforce same-thread reuse.
        conn = sqlite3.connect(SQLITE_DB, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # synchronous is per-connection; NORMAL is safe under the WAL mode
        # enabled once at startup (auth.ensure_observability_tables) and
        # avoids a full fsync-equivalent flush on every commit.
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield ApiDatabaseContext(section=section, sqlite_conn=conn)
        finally:
            conn.close()
        return

    raise HTTPException(status_code=500, detail=f"Nu exista configuratie de baza de date pentru sectiunea '{section}'.")


def get_sqlite_connection(
    context: ApiDatabaseContext = Depends(get_api_database_context),
) -> sqlite3.Connection:
    if context.sqlite_conn is None:
        raise HTTPException(status_code=500, detail=f"Sectiunea '{context.section}' nu foloseste SQLite.")
    return context.sqlite_conn


def get_company_session(
    context: ApiDatabaseContext = Depends(get_api_database_context),
) -> Session:
    if context.postgres_session is None:
        raise HTTPException(status_code=500, detail=f"Sectiunea '{context.section}' nu foloseste PostgreSQL.")
    return context.postgres_session
