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
}
POSTGRES_SECTIONS = {"companii"}


@dataclass
class ApiDatabaseContext:
    section: str
    sqlite_conn: sqlite3.Connection | None = None
    postgres_session: Session | None = None


def _get_api_section(request: Request) -> str:
    segments = [segment for segment in request.url.path.split("/") if segment]
    return segments[1] if len(segments) > 1 else ""


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
        conn = sqlite3.connect(SQLITE_DB)
        conn.row_factory = sqlite3.Row
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