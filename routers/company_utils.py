import re
import unicodedata
from datetime import date, datetime


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