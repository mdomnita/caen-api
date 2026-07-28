from datetime import date

from pydantic import BaseModel


class CompanyOut(BaseModel):
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

    model_config = {"from_attributes": True}


class CompanySearchItem(BaseModel):
    name: str
    cui: int
    county: str | None
    locality: str | None
    registration_number: str | None
    similarity: float


class CompanySearchResponse(BaseModel):
    total: int
    results: list[CompanySearchItem]


class AutocompleteItem(BaseModel):
    name: str
    cui: int


class AutocompleteResponse(BaseModel):
    results: list[AutocompleteItem]


class BilantIndicator(BaseModel):
    label: str
    value: int


class BilantYear(BaseModel):
    year: int
    indicators: list[BilantIndicator]


class BilantResponse(BaseModel):
    cui: int
    name: str
    caen_code: int
    caen_label: str
    years: list[BilantYear]
    warning: str | None = None


class CompanyCaenItem(BaseModel):
    caen_code: str
    is_principal: bool
    caen_version: str | None

    model_config = {"from_attributes": True}


class CompanyCaenResponse(BaseModel):
    cui: int
    principal: CompanyCaenItem | None
    secundare: list[CompanyCaenItem]