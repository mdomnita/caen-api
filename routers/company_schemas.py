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


class FinancialStatsResponse(BaseModel):
    """Aggregate statistics for a filtered group of companies, returned by
    GET /companii/financiar/statistici. `caen` here matches CompanyFinancial.caen (the
    code reported on that year's filing), same convention as FinancialLeaderboardResponse
    -- not the principal/secondary company_caen_codes table."""

    an: int
    camp: str
    judet: str | None
    localitate: str | None
    caen: str | None
    numar_firme: int
    suma: int | None
    medie: float | None
    mediana: float | None
    minim: int | None
    maxim: int | None


class FinancialIndicatorYear(BaseModel):
    """Ratios derived from CompanyFinancial fields for one year -- computed on read,
    not stored. Growth ratios compare against the previous year *in this response*
    (i.e. the closest earlier year actually returned), not necessarily an-1."""

    an: int
    marja_profit: float | None  # profit_net / cifra_afaceri
    cifra_afaceri_per_salariat: float | None  # cifra_afaceri / numar_salariati
    crestere_cifra_afaceri: float | None  # (an - anul anterior din raspuns) / anul anterior
    crestere_profit_net: float | None


class CompanyFinancialIndicatorsResponse(BaseModel):
    """Returned by GET /companii/{cui}/financiar/indicatori."""

    cui: int
    name: str
    years: list[FinancialIndicatorYear]


class CompanyFilterFinancialSnapshot(BaseModel):
    """Financial figures for the `an` requested on GET /companii, restricted to the
    fields that endpoint can filter/sort on (a subset of FINANCIAL_COLUMNS)."""

    an: int
    cifra_afaceri: int | None
    profit_net: int | None
    numar_salariati: int | None
    datorii: int | None
    active_circulante_total: int | None


class CompanyFilterItem(BaseModel):
    cui: int
    name: str
    county: str | None
    locality: str | None
    legal_form: str | None
    caen_principal: str | None
    are_coordonate: bool
    financiar: CompanyFilterFinancialSnapshot | None  # None unless `an` was requested


class CompanyFilterResponse(BaseModel):
    """Returned by GET /companii.

    Unlike CompanySearchResponse.total (rows returned), `total` here is the full
    count of matching companies across all pages, to support offset/limit pagination.
    """

    total: int
    limit: int
    offset: int
    results: list[CompanyFilterItem]


class CompanyComparisonItem(BaseModel):
    """One company's row in a GET /companii/comparatie response.

    All financial/derived fields are None if the company has no company_financials
    row for the resolved year (see CompanyComparisonResponse.an).
    """

    cui: int
    name: str
    an: int | None  # actual year the figures below are for; None if no financial data exists at all
    cifra_afaceri: int | None
    profit_net: int | None
    numar_salariati: int | None
    active_circulante_total: int | None
    datorii: int | None
    marja_profit: float | None  # profit_net / cifra_afaceri
    cifra_afaceri_per_salariat: float | None  # cifra_afaceri / numar_salariati
    crestere_cifra_afaceri: float | None  # vs. the nearest earlier year with data for this company
    crestere_profit_net: float | None


class CompanyComparisonResponse(BaseModel):
    an: int | None  # echoes the requested `an`; None means each company used its own latest year
    cui_negasite: list[int]  # requested CUIs with no matching company
    results: list[CompanyComparisonItem]


# NOU: raspuns pentru GET /companii/{cui}/coordonate
class CompanyCoordonateResponse(BaseModel):
    cui: int
    latitude: float
    longitude: float
    score: float | None
    sursa: str  # "stocat" (deja geocodificat de scripts/geocode_companies.py) sau "live"
    adresa_folosita: str