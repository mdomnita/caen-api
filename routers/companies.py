from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy import case, desc, func, literal, or_, select
from sqlalchemy.orm import Session

from auth import limiter, _dynamic_limit
from api_dependencies import get_company_session
from routers.company_models import Company
from routers.company_schemas import (
    AutocompleteItem,
    AutocompleteResponse,
    CompanyOut,
    CompanySearchItem,
    CompanySearchResponse,
)
from routers.company_utils import normalize_company_name

router = APIRouter(prefix="/companii", tags=["Companies"])


# def _search_filter(normalized_query: str, session: Session):
#     contains_filter = Company.normalized_name.contains(normalized_query)
#     if session.bind and session.bind.dialect.name == "postgresql":
#         similarity_expr = func.similarity(Company.normalized_name, normalized_query)
#         return or_(contains_filter, similarity_expr >= 0.2), similarity_expr
#     return contains_filter, literal(0.0)


def _search_filter(normalized_query: str, session: Session):
    if session.bind and session.bind.dialect.name == "postgresql":
        similarity_expr = func.similarity(Company.normalized_name, normalized_query)
        prefix_filter = Company.normalized_name.like(f"{normalized_query}%")
        trigram_filter = Company.normalized_name.op("%")(normalized_query)
        return or_(prefix_filter, trigram_filter), similarity_expr
    return Company.normalized_name.contains(normalized_query), literal(0.0)


def _prefix_filter(normalized_query: str, session: Session):
    if session.bind and session.bind.dialect.name == "postgresql":
        return Company.normalized_name.like(f"{normalized_query}%")
    return Company.normalized_name.startswith(normalized_query)

@router.get("/search", response_model=CompanySearchResponse)
@limiter.limit(_dynamic_limit)
def search_companies(
    request: Request,
    q: str = Query(..., min_length=2, description="Text pentru cautare dupa denumire"),
    limit: int = Query(20, ge=1, le=50),
    session: Session = Depends(get_company_session),
):
    normalized_query = normalize_company_name(q)
    if not normalized_query:
        return CompanySearchResponse(total=0, results=[])

    filter_clause, similarity_expr = _search_filter(normalized_query, session)
    prefix_rank = case((Company.normalized_name.startswith(normalized_query), 0), else_=1)

    rows_stmt = (
        select(Company, similarity_expr.label("similarity"))
        .where(filter_clause)
        .order_by(prefix_rank, desc("similarity"), Company.name)
        .limit(limit)
    )

    rows = session.execute(rows_stmt).all()

    results = [
        CompanySearchItem(
            name=company.name,
            cui=company.cui,
            county=company.county,
            locality=company.locality,
            registration_number=company.registration_number,
            similarity=float(score or 0.0),
        )
        for company, score in rows
    ]
    return CompanySearchResponse(total=len(results), results=results)


@router.get("/autocomplete", response_model=AutocompleteResponse)
@limiter.limit(_dynamic_limit)
def autocomplete_companies(
    request: Request,
    q: str = Query(..., min_length=2, description="Prefix sau fragment din denumire"),
    limit: int = Query(10, ge=1, le=20),
    session: Session = Depends(get_company_session),
):
    normalized_query = normalize_company_name(q)
    if not normalized_query:
        return AutocompleteResponse(results=[])

    filter_clause = _prefix_filter(normalized_query, session)
    prefix_rank = case(
        (Company.normalized_name == normalized_query, 0),
        else_=1,
    )
    stmt = (
        select(Company.name, Company.cui)
        .where(filter_clause)
        .order_by(prefix_rank, Company.name)
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    return AutocompleteResponse(results=[AutocompleteItem(name=name, cui=cui) for name, cui in rows])


@router.get("/{cui}", response_model=CompanyOut)
@limiter.limit(_dynamic_limit)
def get_company(
    request: Request,
    cui: int = Path(..., ge=1, description="Cod unic de identificare"),
    session: Session = Depends(get_company_session),
):
    company = session.scalar(select(Company).where(Company.cui == cui))
    if company is None:
        raise HTTPException(status_code=404, detail=f"Compania cu CUI {cui} nu a fost gasita.")
    return company