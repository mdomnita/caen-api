"""Search legal representatives and the companies they represent."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from api_dependencies import get_company_session
from auth import _dynamic_limit, limiter
from routers.company_models import Company, CompanyRepresentative
from routers.representative_schemas import RepresentativeSearchItem, RepresentativeSearchResponse


router = APIRouter(prefix="/representatives", tags=["Representatives"])


def _representative_name_filter(query: str, session: Session):
    """Build the indexed PostgreSQL fuzzy match, with a deterministic test fallback."""
    if session.bind and session.bind.dialect.name == "postgresql":
        similarity = func.word_similarity(query, CompanyRepresentative.nume)
        # The <% operator is supported by the GIN trigram index declared on `nume`.
        return literal(query).op("<%")(CompanyRepresentative.nume), similarity

    contains = func.lower(CompanyRepresentative.nume).contains(query.lower())
    return contains, literal(1.0)


@router.get(
    "/search",
    response_model=RepresentativeSearchResponse,
    summary="Cauta reprezentanti legali",
    description=(
        "Cautare fuzzy dupa numele reprezentantului, cu filtrare optionala dupa rol. "
        "`total` reprezinta toate potrivirile, inainte de aplicarea paginarii."
    ),
)
@limiter.limit(_dynamic_limit)
def search_representatives(
    request: Request,
    q: str = Query(..., min_length=2, description="Numele reprezentantului"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    role: str | None = Query(None, min_length=1, description="Calitatea/rolul exact"),
    session: Session = Depends(get_company_session),
):
    query = q.strip()
    if not query:
        return RepresentativeSearchResponse(total=0, results=[])

    name_filter, similarity = _representative_name_filter(query, session)
    filters = [name_filter]
    if role is not None:
        filters.append(func.lower(CompanyRepresentative.calitate) == role.strip().lower())

    # Count separately so `total` remains useful when limit/offset select one page.
    total = session.scalar(
        select(func.count())
        .select_from(CompanyRepresentative)
        .join(Company, Company.id == CompanyRepresentative.company_id)
        .where(*filters)
    ) or 0

    rows = session.execute(
        select(
            CompanyRepresentative.nume.label("representative_name"),
            CompanyRepresentative.calitate.label("role"),
            Company.name.label("company_name"),
            Company.cui,
            Company.county,
            Company.locality,
            similarity.label("similarity"),
        )
        .join(Company, Company.id == CompanyRepresentative.company_id)
        .where(*filters)
        .order_by(similarity.desc(), CompanyRepresentative.nume, Company.cui)
        .limit(limit)
        .offset(offset)
    ).all()

    return RepresentativeSearchResponse(
        total=total,
        results=[
            RepresentativeSearchItem(
                representative_name=row.representative_name,
                role=row.role,
                company_name=row.company_name,
                cui=row.cui,
                county=row.county,
                locality=row.locality,
                similarity=float(row.similarity or 0.0),
            )
            for row in rows
        ],
    )
