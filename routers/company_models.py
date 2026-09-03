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
    # NOU: stare firma (scripts/update_company_stare.py, din OD_STARE_FIRMA.CSV) si CAEN
    # principal (scripts/update_company_caen_principal.py, live ANAF PlatitorTva v9) --
    # informatii pe care exportul bulk ONRC od_caen_autorizat.csv nu le contine.
    is_active: Mapped[bool | None] = mapped_column(Boolean)
    stare_verificata_la: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    caen_principal_status: Mapped[str | None] = mapped_column(String(32))  # "ok" | "cod_lipsa" | "not_found" | "error"
    caen_principal_verificat_la: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # NOU: fereastra de inchidere (scripts/derive_company_closure_window.py), derivata din
    # instantanee ONRC istorice (temp/onrc/*firme_radiate*, *firme_neradiate*) -- nu o data
    # exacta de radiere (nu exista nicaieri in sursele deschise), doar o incadrare intre ultima
    # instantanee in care firma a fost vazuta activa si prima in care a fost vazuta inactiva.
    ultima_data_activa_cunoscuta: Mapped[date | None] = mapped_column(Date)
    prima_data_inactiva_cunoscuta: Mapped[date | None] = mapped_column(Date)
    prima_data_radiata_cunoscuta: Mapped[date | None] = mapped_column(Date)
    fereastra_inchidere_tip: Mapped[str | None] = mapped_column(String(32))  # "incadrata" | "necunoscuta_inainte_de_2015"
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
    # name search/autocomplete, registration-number lookup, filtering by geocode status,
    # and filtering by judet/localitate (GET /companii, GET /companii/financiar/statistici).
    __table_args__ = (
        Index("ix_companies_cui", "cui", unique=True),
        Index("ix_companies_normalized_name", "normalized_name"),
        Index("ix_companies_registration_number", "registration_number"),
        Index("ix_companies_geocode_status", "geocode_status"),
        Index("ix_companies_county", "county"),
        Index("ix_companies_locality", "locality"),
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


class CompanyFinancialStats(Base):
    """Precomputed aggregate statistics over CompanyFinancial, one row per
    (an, camp, judet, caen) -- judet/caen NULL means "all". Built offline by
    scripts/refresh_company_financial_stats.py (a single pass over company_financials,
    not a live query) and read by GET /companii/financiar/statistici for the
    national / judet-only / caen-only cases; other filter combinations (localitate,
    or judet+caen together) aren't precomputed and fall back to a live query.
    """

    __tablename__ = "company_financial_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    an: Mapped[int] = mapped_column(nullable=False)
    camp: Mapped[str] = mapped_column(String(64), nullable=False)
    judet: Mapped[str | None] = mapped_column(String(128))
    caen: Mapped[str | None] = mapped_column(String(4))
    numar_firme: Mapped[int] = mapped_column(nullable=False)
    suma: Mapped[int | None] = mapped_column(BigInteger)
    medie: Mapped[float | None] = mapped_column(Float)
    mediana: Mapped[float | None] = mapped_column(Float)
    minim: Mapped[int | None] = mapped_column(BigInteger)
    maxim: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=datetime.now(timezone.utc),
    )

    __table_args__ = (
        # Lookup path used by the endpoint: exact match on all four columns.
        Index("ix_company_financial_stats_lookup", "an", "camp", "judet", "caen"),
    )


class CompanyFiscalInfo(Base):
    """One row per company (1:1, unlike CompanyCaenCode/CompanyFinancial), from MFP's
    taxpayer registry (temp/mfp/date_de_identificare_platitori_*/..._a.csv -- PJ only,
    matched by COD_FISCAL == companies.cui directly, not registration_number). Populated
    by scripts/import_company_fiscal_info.py.

    indicatori_fiscali_raw holds the ~25 IMP*/CONT*/ACCIZE200 DA/NU flags from the source
    verbatim ("IMP100=DA;IMP120=NU;...") -- no legend for their individual meaning was
    found in any downloaded dataset, so they aren't split into named columns.
    """

    __tablename__ = "company_fiscal_info"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    tva_platitor: Mapped[bool | None] = mapped_column(Boolean)
    data_inregistrare_fiscala: Mapped[date | None] = mapped_column(Date)
    data_radiere_fiscala: Mapped[date | None] = mapped_column(Date)
    stare_fiscala: Mapped[str | None] = mapped_column(String(64))
    data_stare_fiscala: Mapped[date | None] = mapped_column(Date)
    telefon: Mapped[str | None] = mapped_column(String(32))
    fax: Mapped[str | None] = mapped_column(String(32))
    adresa_fiscala_localitate: Mapped[str | None] = mapped_column(String(255))
    adresa_fiscala_judet: Mapped[str | None] = mapped_column(String(128))
    adresa_fiscala_strada: Mapped[str | None] = mapped_column(String(255))
    adresa_fiscala_numar: Mapped[str | None] = mapped_column(String(32))
    adresa_fiscala_detalii: Mapped[str | None] = mapped_column(Text)
    cod_postal_fiscal: Mapped[str | None] = mapped_column(String(32))
    indicatori_fiscali_raw: Mapped[str | None] = mapped_column(Text)
    actualizat_la: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_company_fiscal_info_company_id", "company_id", unique=True),
    )


class CompanyRepresentative(Base):
    """Legal representative (administrator, lichidator, etc.) of a company, from ONRC's
    od_reprezentanti_legali.csv (matched by COD_INMATRICULARE == companies.registration_number,
    like CompanyCaenCode). One company can have several. No CNP in the source. Populated by
    scripts/import_company_representatives.py.
    """

    __tablename__ = "company_representatives"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    nume: Mapped[str] = mapped_column(Text, nullable=False)
    calitate: Mapped[str | None] = mapped_column(String(128))
    data_nasterii: Mapped[date | None] = mapped_column(Date)
    localitate_nasterii: Mapped[str | None] = mapped_column(String(255))
    judet_nasterii: Mapped[str | None] = mapped_column(String(128))
    tara_nasterii: Mapped[str | None] = mapped_column(String(128))
    localitate: Mapped[str | None] = mapped_column(String(255))
    judet: Mapped[str | None] = mapped_column(String(128))
    tara: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        Index("ix_company_representatives_company_id", "company_id"),
        Index("ix_company_representatives_nume", "nume"),
        UniqueConstraint(
            "company_id", "nume", "calitate", name="uq_company_representatives_company_nume_calitate"
        ),
    )
