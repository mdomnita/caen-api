import re
import unicodedata
from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from routers.company_models import Company


def normalize_company_name(value: str) -> str:
    value = value.strip().lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def parse_ro_date(value: str | None) -> date | None:
    cleaned = clean_text(value)
    if not cleaned:
        return None

    for date_format in ("%d/%m/%Y", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue

    date_part = cleaned.split()[0]
    try:
        return datetime.strptime(date_part, "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_cui(value: str | None) -> int | None:
    cleaned = clean_text(value)
    if not cleaned:
        return None
    digits = re.sub(r"\D+", "", cleaned)
    return int(digits) if digits else None


# --- NOU: adresa pentru geocodare pe cerere (GET /companii/{cui}/coordonate) ---
def build_company_address(company: "Company") -> str | None:
    """Build a single-line address for on-demand geocoding, from the subset
    of CompanyOut's address fields that actually affect the geocoded
    coordinate (street/street_number/sector/locality/county/postal_code/
    country). Apartment-level fields (building, staircase, floor, apartment)
    are intentionally excluded -- ArcGIS geocodes to street/building level,
    so they add noise to the query without changing the result.

    Returns None if there isn't enough address data to attempt geocoding.
    """
    parts = [
        " ".join(p for p in [company.street, company.street_number] if p),
        company.sector,
        company.locality,
        company.county,
        company.postal_code,
        company.country or "Romania",
    ]
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return None
    return ", ".join(parts)