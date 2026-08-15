"""Pydantic response models for the /companii routes in routers/companies.py.

These are the API-facing shapes; the ORM tables they're built from live in
routers/company_models.py.
"""

from datetime import date

from pydantic import BaseModel


class CompanyOut(BaseModel):
    """Full company record, returned by GET /companii/{cui}."""


    name: str
    cui: int
    registration_number: str | None
    registration_date: date | None
    euid: str | None
    legal_form: str | None
    country: str | None
    county: str | None
    locality: str | None
    street: str | None
    street_number: str | None
    building: str | None
    staircase: str | None
    floor: str | None
    apartment: str | None
    postal_code: str | None
    sector: str | None
    address_extra: str | None
    website: str | None
    parent_company_country: str | None
    # NOU: coordonate stocate (populate de scripts/geocode_companies.py); None daca firma
    # nu a fost inca geocodificata. Nu necesita nicio schimbare in handler-ul GET /{cui} --
    # from_attributes=True le preia direct de pe obiectul ORM Company.
    latitude: float | None
    longitude: float | None

    model_config = {"from_attributes": True}


class CompanySearchItem(BaseModel):
    """One fuzzy-search hit, returned by GET /companii/search."""

    name: str
    cui: int
    county: str | None
    locality: str | None
    registration_number: str | None
    similarity: float  # 0..1 trigram/prefix match score; see routers/companies.py::_search_filter


class CompanySearchResponse(BaseModel):
    total: int  # number of rows in `results`, not the total matches in the DB
    results: list[CompanySearchItem]


class AutocompleteItem(BaseModel):
    """Lightweight type-ahead suggestion, returned by GET /companii/autocomplete."""

    name: str
    cui: int


class AutocompleteResponse(BaseModel):
    results: list[AutocompleteItem]


class BilantIndicator(BaseModel):
    """A single ANAF-reported financial indicator (label + value) for one fiscal year."""

    label: str
    value: int


class BilantYear(BaseModel):
    year: int
    indicators: list[BilantIndicator]


class BilantResponse(BaseModel):
    """Live ANAF bilant data, returned by GET /companii/{cui}/bilant[/ultimul-an].

    Distinct from CompanyFinancialsResponse below: this is fetched from the ANAF
    webservice on every request, not read from the local company_financials table.
    """

    cui: int
    name: str
    caen_code: int
    caen_label: str
    years: list[BilantYear]
    warning: str | None = None  # set when multiple years were requested (slower, parallel ANAF calls)


class CompanyCaenItem(BaseModel):
    caen_code: str
    is_principal: bool
    caen_version: str | None

    model_config = {"from_attributes": True}


class CompanyCaenResponse(BaseModel):
    """Principal + secondary CAEN codes for a company, returned by GET /companii/{cui}/caen."""

    cui: int
    principal: CompanyCaenItem | None
    secundare: list[CompanyCaenItem]


class CompanyFinancialYear(BaseModel):
    """One fiscal year of stored financial data for a company.

    `values` only contains the fields the caller asked for (via `campuri`) so a
    field being absent from the dict is distinguishable from it being null in the DB.
    """

    an: int
    sursa: str
    caen: str | None
    values: dict[str, int | None]


class CompanyFinancialsResponse(BaseModel):
    """Returned by GET /companii/{cui}/financiar."""

    cui: int
    name: str
    fields: list[str]  # echoes which keys are present in each year's `values`
    years: list[CompanyFinancialYear]


class FinancialSeriesPoint(BaseModel):
    an: int
    valoare: int | None  # null if the field wasn't reported that year


class CompanyFinancialSeriesResponse(BaseModel):
    """Single-indicator time series, returned by GET /companii/{cui}/financiar/evolutie."""

    cui: int
    name: str
    camp: str
    puncte: list[FinancialSeriesPoint]


class FinancialLeaderboardItem(BaseModel):
    cui: int
    name: str
    caen: str | None  # CAEN code reported alongside this company's row for the ranked year
    county: str | None
    valoare: int


class FinancialLeaderboardResponse(BaseModel):
    """Returned by GET /companii/financiar/clasament."""

    an: int
    camp: str
    results: list[FinancialLeaderboardItem]


# NOU: raspuns pentru GET /companii/{cui}/coordonate
class CompanyCoordonateResponse(BaseModel):
    cui: int
    latitude: float
    longitude: float
    score: float | None
    sursa: str  # "stocat" (deja geocodificat de scripts/geocode_companies.py) sau "live"
    adresa_folosita: str