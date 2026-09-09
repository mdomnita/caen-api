"""API response models for representative search."""

from pydantic import BaseModel


class RepresentativeSearchItem(BaseModel):
    representative_name: str
    role: str | None
    company_name: str
    cui: int
    county: str | None
    locality: str | None
    similarity: float


class RepresentativeSearchResponse(BaseModel):
    total: int
    results: list[RepresentativeSearchItem]
