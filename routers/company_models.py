from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from routers.company_database import Base


class Company(Base):
    """One row per Romanian company, keyed by CUI (unique fiscal identification code)."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(512), nullable=False)
    cui: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    registration_number: Mapped[str | None] = mapped_column(String(64))
    registration_date: Mapped[date | None] = mapped_column(Date)
    euid: Mapped[str | None] = mapped_column(String(128))
    legal_form: Mapped[str | None] = mapped_column(String(64))
    country: Mapped[str | None] = mapped_column(String(128))
    county: Mapped[str | None] = mapped_column(String(128))
    locality: Mapped[str | None] = mapped_column(String(255))
    street: Mapped[str | None] = mapped_column(String(255))
    street_number: Mapped[str | None] = mapped_column(String(64))
    building: Mapped[str | None] = mapped_column(String(64))
    staircase: Mapped[str | None] = mapped_column(String(64))
    floor: Mapped[str | None] = mapped_column(String(64))
    apartment: Mapped[str | None] = mapped_column(String(64))
    postal_code: Mapped[str | None] = mapped_column(String(32))
    sector: Mapped[str | None] = mapped_column(String(32))
    address_extra: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(String(255))
    parent_company_country: Mapped[str | None] = mapped_column(String(128))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    geocode_score: Mapped[float | None] = mapped_column(Float)
    geocode_status: Mapped[str | None] = mapped_column(String(32))
    geocoded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # when latitude/longitude were last resolved
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=datetime.now(timezone.utc),
    )

    # Indexes backing lookups/filters used by routers/companies.py: exact CUI lookup,
    # name search/autocomplete, registration-number lookup, and filtering by geocode status.
    __table_args__ = (
        Index("ix_companies_cui", "cui", unique=True),
        Index("ix_companies_normalized_name", "normalized_name"),
        Index("ix_companies_registration_number", "registration_number"),
        Index("ix_companies_geocode_status", "geocode_status"),
    )


class CompanyCaenCode(Base):
    """CAEN activity code(s) registered for a company; one row is flagged as the principal code."""

    __tablename__ = "company_caen_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    caen_code: Mapped[str] = mapped_column(String(4), nullable=False)
    is_principal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # True for the firm's main activity code
    caen_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        Index("ix_company_caen_codes_company_id", "company_id"),
        Index("ix_company_caen_codes_caen_code", "caen_code"),
        UniqueConstraint("company_id", "caen_code", name="uq_company_caen_codes_company_caen"),
    )


# Numeric columns of CompanyFinancial exposed for selection/import -- kept as a single
# canonical list so scripts/import_company_financials.py and the /financiar endpoint
# in routers/companies.py can't drift apart.
FINANCIAL_COLUMNS = [
    "cifra_afaceri",
    "venituri_totale",
    "cheltuieli_totale",
    "profit_brut",
    "profit_net",
    "capitaluri_total",
    "capital_social",
    "active_imobilizate_total",
    "active_circulante_total",
    "stocuri",
    "creante",
    "casa_conturi",
    "datorii",
    "provizioane",
    "patrimoniul_public",
    "patrimoniul_regiei",
    "numar_salariati",
]


class CompanyFinancial(Base):
    """One row per company per fiscal year of imported financial statement data.

    Populated by scripts/import_company_financials.py from MFP (Ministry of Public
    Finance) situatii financiare datasets; served by GET /companii/{cui}/financiar.
    """

    __tablename__ = "company_financials"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    an: Mapped[int] = mapped_column(nullable=False)  # fiscal year
    sursa: Mapped[str] = mapped_column(String(16), nullable=False)  # data source/report type (e.g. bilant lung/prescurtat/IFRS)
    caen: Mapped[str | None] = mapped_column(String(4))  # CAEN code reported alongside this year's statement
    cifra_afaceri: Mapped[int | None] = mapped_column(BigInteger)
    venituri_totale: Mapped[int | None] = mapped_column(BigInteger)
    cheltuieli_totale: Mapped[int | None] = mapped_column(BigInteger)
    profit_brut: Mapped[int | None] = mapped_column(BigInteger)
    profit_net: Mapped[int | None] = mapped_column(BigInteger)
    capitaluri_total: Mapped[int | None] = mapped_column(BigInteger)
    capital_social: Mapped[int | None] = mapped_column(BigInteger)
    active_imobilizate_total: Mapped[int | None] = mapped_column(BigInteger)
    active_circulante_total: Mapped[int | None] = mapped_column(BigInteger)
    stocuri: Mapped[int | None] = mapped_column(BigInteger)
    creante: Mapped[int | None] = mapped_column(BigInteger)
    casa_conturi: Mapped[int | None] = mapped_column(BigInteger)
    datorii: Mapped[int | None] = mapped_column(BigInteger)
    provizioane: Mapped[int | None] = mapped_column(BigInteger)
    patrimoniul_public: Mapped[int | None] = mapped_column(BigInteger)
    patrimoniul_regiei: Mapped[int | None] = mapped_column(BigInteger)
    numar_salariati: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_company_financials_company_id", "company_id"),
        Index("ix_company_financials_an", "an"),
        UniqueConstraint("company_id", "an", name="uq_company_financials_company_an"),
    )