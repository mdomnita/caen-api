from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from routers.company_database import Base


class Company(Base):
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
        Index("ix_companies_cui", "cui", unique=True),
        Index("ix_companies_normalized_name", "normalized_name"),
        Index("ix_companies_registration_number", "registration_number"),
    )


class CompanyCaenCode(Base):
    __tablename__ = "company_caen_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    caen_code: Mapped[str] = mapped_column(String(4), nullable=False)
    is_principal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
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