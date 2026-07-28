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


def init_postgres() -> None:
    from routers.company_models import Company

    current_engine = _get_engine()

    with current_engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        Base.metadata.create_all(bind=connection)
        if connection.dialect.name == "postgresql":
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_companies_normalized_name_trgm "
                    "ON companies USING gin (normalized_name gin_trgm_ops)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_companies_registration_number "
                    "ON companies (registration_number)"
                )
            )