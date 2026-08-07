import os
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DEFAULT_DATABASE_URL = "postgresql+psycopg://companies:companies@db:5432/companies"

engine: Engine | None = None
_configured_database_url: str | None = None
SessionLocal = sessionmaker(autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _get_database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def _get_engine() -> Engine:
    global engine, _configured_database_url

    database_url = _get_database_url()
    if engine is None or database_url != _configured_database_url:
        engine = create_engine(database_url, pool_pre_ping=True)
        SessionLocal.configure(bind=engine)
        _configured_database_url = database_url
    return engine


def get_session() -> Generator[Session, None, None]:
    _get_engine()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _existing_columns(connection, table_name: str) -> set[str]:
    rows = connection.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
        {"t": table_name},
    )
    return {row[0] for row in rows}


def _existing_indexes(connection, table_name: str) -> set[str]:
    rows = connection.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = :t"),
        {"t": table_name},
    )
    return {row[0] for row in rows}


_COMPANIES_GEOCODE_COLUMNS = {
    "latitude": "ALTER TABLE companies ADD COLUMN latitude DOUBLE PRECISION",
    "longitude": "ALTER TABLE companies ADD COLUMN longitude DOUBLE PRECISION",
    "geocode_score": "ALTER TABLE companies ADD COLUMN geocode_score DOUBLE PRECISION",
    "geocode_status": "ALTER TABLE companies ADD COLUMN geocode_status VARCHAR(32)",
    "geocoded_at": "ALTER TABLE companies ADD COLUMN geocoded_at TIMESTAMP WITH TIME ZONE",
}

_COMPANIES_INDEXES = {
    "ix_companies_normalized_name_trgm": (
        "CREATE INDEX ix_companies_normalized_name_trgm "
        "ON companies USING gin (normalized_name gin_trgm_ops)"
    ),
    "ix_companies_registration_number": (
        "CREATE INDEX ix_companies_registration_number ON companies (registration_number)"
    ),
    "ix_companies_geocode_status": (
        "CREATE INDEX ix_companies_geocode_status ON companies (geocode_status)"
    ),
}


def init_postgres() -> None:
    from routers.company_models import Company

    current_engine = _get_engine()

    with current_engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        Base.metadata.create_all(bind=connection)
        if connection.dialect.name == "postgresql":
            # init_postgres() runs on every API startup and every script
            # invocation that touches `companies` (geocode_companies.py,
            # import/update_companies.py). ALTER TABLE / CREATE INDEX take an
            # AccessExclusiveLock even with IF NOT EXISTS (Postgres has no
            # way to skip the check without it) — harmless in isolation, but
            # if it races another process's in-flight UPDATE transaction on
            # `companies`, the two can deadlock (a long-running batch's next
            # RowExclusiveLock request queues behind this AccessExclusiveLock
            # request, which is itself waiting on that same batch's
            # already-held lock). So: check information_schema/pg_indexes
            # first (plain reads, no lock on `companies`) and only run the
            # DDL when something is actually missing — the common case
            # (schema already migrated) then takes no lock on `companies` at
            # all.
            existing_columns = _existing_columns(connection, "companies")
            existing_indexes = _existing_indexes(connection, "companies")

            for column_name, ddl in _COMPANIES_GEOCODE_COLUMNS.items():
                if column_name not in existing_columns:
                    connection.execute(text(ddl))

            for index_name, ddl in _COMPANIES_INDEXES.items():
                if index_name not in existing_indexes:
                    connection.execute(text(ddl))