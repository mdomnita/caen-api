"""Routes under /companii: company lookup/search plus CAEN codes, geocoding, and
financial data (both live ANAF bilant and the locally imported company_financials table).
"""

import asyncio
from datetime import date

import httpx
import requests
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy import and_, desc, func, literal, or_, select
from sqlalchemy.orm import Session

from auth import limiter, _dynamic_limit
from api_dependencies import get_company_session
from routers.company_models import (
    FINANCIAL_COLUMNS,
    Company,
    CompanyCaenCode,
    CompanyFinancial,
    CompanyFinancialStats,
)
from routers.company_schemas import (
    AutocompleteItem,
    AutocompleteResponse,
    BilantIndicator,
    BilantResponse,
    BilantYear,
    CompanyCaenItem,
    CompanyCaenResponse,
    CompanyComparisonItem,
    CompanyComparisonResponse,
    CompanyCoordonateResponse,
    CompanyFilterFinancialSnapshot,
    CompanyFilterItem,
    CompanyFilterResponse,
    CompanyFinancialIndicatorsResponse,
    CompanyFinancialSeriesResponse,
    CompanyFinancialsResponse,
    CompanyFinancialYear,
    CompanyOut,
    CompanySearchItem,
    CompanySearchResponse,
    FinancialIndicatorYear,
    FinancialLeaderboardItem,
    FinancialLeaderboardResponse,
    FinancialSeriesPoint,
    FinancialStatsResponse,
)
from routers.company_utils import build_company_address, normalize_company_name
# NOU: pentru GET /companii/{cui}/coordonate
from services.geocoding import GeocodingProvider, get_geocoding_provider

_ANAF_BILANT_URL = "https://webservicesp.anaf.ro/bilant"


async def _fetch_bilant_year(client: httpx.AsyncClient, cui: int, year: int) -> dict | None:
    """Fetch one year of ANAF bilant data for a CUI; None on any failure or no-data
    response (missing/empty "i" indicator list), so callers can treat both the same."""
    try:
        r = await client.get(_ANAF_BILANT_URL, params={"an": year, "cui": cui}, timeout=10.0)
        r.raise_for_status()
        data = r.json()
        return data if data.get("i") else None
    except Exception:
        return None

router = APIRouter(prefix="/companii", tags=["Companies"])


# def _search_filter(normalized_query: str, session: Session):
#     contains_filter = Company.normalized_name.contains(normalized_query)
#     if session.bind and session.bind.dialect.name == "postgresql":
#         similarity_expr = func.similarity(Company.normalized_name, normalized_query)
#         return or_(contains_filter, similarity_expr >= 0.2), similarity_expr
#     return contains_filter, literal(0.0)


def _search_filter(normalized_query: str, session: Session):
    if session.bind and session.bind.dialect.name == "postgresql":
        # word_similarity finds the best matching substring of the company name,
        # so "metro" scores ~1.0 against "sc metro cash carry srl".
        # The <% operator uses the GIN trigram index directly (single scan, no BitmapOR).
        word_sim_expr = func.word_similarity(normalized_query, Company.normalized_name)
        filter_clause = literal(normalized_query).op("<%")(Company.normalized_name)
        return filter_clause, word_sim_expr
    return Company.normalized_name.contains(normalized_query), literal(0.0)


def _prefix_filter(normalized_query: str, session: Session):
    """Prefix-only match (no trigram similarity) backing GET /autocomplete."""
    if session.bind and session.bind.dialect.name == "postgresql":
        return Company.normalized_name.like(f"{normalized_query}%")
    return Company.normalized_name.startswith(normalized_query)

@router.get(
    "/search",
    response_model=CompanySearchResponse,
    summary="Cauta firme dupa denumire",
    description=(
        "Cautare fuzzy dupa denumire folosind prefix match si similaritate trigram (`pg_trgm`). "
        "Returneaza campuri usoare: `name`, `cui`, `county`, `locality`, `similarity`. Rezultatele "
        "sunt ordonate dupa rangul de prefix, apoi dupa scorul de similaritate descrescator. "
        "`total` reflecta numarul de randuri returnate, nu numarul total de potriviri din baza de date."
    ),
)
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

    rows_stmt = (
        select(
            Company.name,
            Company.cui,
            Company.county,
            Company.locality,
            similarity_expr.label("similarity"),
        )
        .where(filter_clause)
        .order_by(desc(similarity_expr), Company.normalized_name)
        .limit(limit)
    )

    rows = session.execute(rows_stmt).all()

    results = [
        CompanySearchItem(
            name=row.name,
            cui=row.cui,
            county=row.county,
            locality=row.locality,
            registration_number=None,
            similarity=float(row.similarity or 0.0),
        )
        for row in rows
    ]
    return CompanySearchResponse(total=len(results), results=results)


@router.get(
    "/autocomplete",
    response_model=AutocompleteResponse,
    summary="Sugestii de denumire firma (type-ahead)",
    description=(
        "Cautare doar dupa prefix (index B-tree, fara trigram). Returneaza `name` si `cui`, "
        "ordonate alfabetic dupa denumirea normalizata. Cea mai rapida optiune pentru UI-uri de tip type-ahead."
    ),
)
@limiter.limit(_dynamic_limit)
def autocomplete_companies(
    request: Request,
    q: str = Query(..., min_length=2, description="Prefix din denumire"),
    limit: int = Query(10, ge=1, le=20),
    session: Session = Depends(get_company_session),
):
    normalized_query = normalize_company_name(q)
    if not normalized_query:
        return AutocompleteResponse(results=[])

    filter_clause = _prefix_filter(normalized_query, session)
    stmt = (
        select(Company.name, Company.cui)
        .where(filter_clause)
        .order_by(Company.normalized_name)
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    return AutocompleteResponse(results=[AutocompleteItem(name=name, cui=cui) for name, cui in rows])


# Company-table fields sortable on GET /companii; financial fields require `an` (see below).
_FILTER_SORT_COMPANY_FIELDS = {
    "nume": Company.normalized_name,
    "cui": Company.cui,
}
# CompanyFinancial fields usable as both a min/max filter and a sort key on GET /companii.
_FILTER_SORT_FINANCIAL_FIELDS = {
    "cifra_afaceri": CompanyFinancial.cifra_afaceri,
    "profit_net": CompanyFinancial.profit_net,
    "salariati": CompanyFinancial.numar_salariati,
    "datorii": CompanyFinancial.datorii,
    "active": CompanyFinancial.active_circulante_total,  # active circulante totale; nu exista un camp de "active totale"
}
_ALL_FILTER_SORT_FIELDS = sorted(set(_FILTER_SORT_COMPANY_FIELDS) | set(_FILTER_SORT_FINANCIAL_FIELDS))
_MAX_FILTER_LIMIT = 200


@router.get(
    "",
    response_model=CompanyFilterResponse,
    summary="Filtrare avansata firme",
    description=(
        "Cauta firme dupa criterii combinate: judet/localitate, cod CAEN (principal sau secundar/"
        "autorizat), forma juridica, prezenta coordonatelor geografice, si praguri financiare "
        "(cifra de afaceri, profit net, salariati, datorii, active circulante) pentru un an fiscal "
        "dat. Utila pentru analiza de piata si identificarea de potentiali clienti/parteneri, spre "
        "deosebire de `/search`, care cauta doar dupa denumire. Statusul juridic (activa/radiata) nu "
        "este stocat in prezent, deci nu poate fi filtrat. Filtrarea sau sortarea dupa campuri "
        "financiare necesita parametrul `an`. `total` reflecta numarul total de potriviri (util "
        "pentru paginare cu `limit`/`offset`), nu doar randurile din pagina curenta."
    ),
)
@limiter.limit(_dynamic_limit)
def list_companies(
    request: Request,
    judet: str | None = Query(default=None, description="Judet (potrivire exacta)."),
    localitate: str | None = Query(default=None, description="Localitate (potrivire exacta)."),
    caen: str | None = Query(default=None, description="Cod CAEN, principal sau secundar (autorizat)."),
    forma_juridica: str | None = Query(default=None, description="Forma juridica (ex: SRL), potrivire exacta."),
    are_coordonate: bool | None = Query(
        default=None,
        description="true: doar firme cu latitudine/longitudine stocate. false: doar firme fara.",
    ),
    an: int | None = Query(default=None, description="Anul fiscal pentru pragurile si sortarea financiara."),
    cifra_afaceri_min: int | None = Query(default=None, description="Necesita `an`."),
    cifra_afaceri_max: int | None = Query(default=None, description="Necesita `an`."),
    profit_net_min: int | None = Query(default=None, description="Necesita `an`."),
    profit_net_max: int | None = Query(default=None, description="Necesita `an`."),
    salariati_min: int | None = Query(default=None, description="Necesita `an`."),
    salariati_max: int | None = Query(default=None, description="Necesita `an`."),
    datorii_min: int | None = Query(default=None, description="Necesita `an`."),
    datorii_max: int | None = Query(default=None, description="Necesita `an`."),
    active_min: int | None = Query(default=None, description="Prag minim active circulante totale. Necesita `an`."),
    active_max: int | None = Query(default=None, description="Prag maxim active circulante totale. Necesita `an`."),
    sort: str | None = Query(
        default=None,
        description=(
            "Camp de sortare, optional prefixat cu `-` pentru descrescator (ex: -cifra_afaceri). "
            f"Valori valide: {', '.join(_ALL_FILTER_SORT_FIELDS)}. Implicit: denumire crescator."
        ),
    ),
    limit: int = Query(50, ge=1, le=_MAX_FILTER_LIMIT),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_company_session),
):
    financial_filters = {
        "cifra_afaceri": (cifra_afaceri_min, cifra_afaceri_max),
        "profit_net": (profit_net_min, profit_net_max),
        "salariati": (salariati_min, salariati_max),
        "datorii": (datorii_min, datorii_max),
        "active": (active_min, active_max),
    }
    wants_financial_filter = any(v is not None for pair in financial_filters.values() for v in pair)

    descending = False
    sort_field = None
    if sort:
        descending = sort.startswith("-")
        sort_field = sort[1:] if descending else sort
        if sort_field not in _FILTER_SORT_COMPANY_FIELDS and sort_field not in _FILTER_SORT_FINANCIAL_FIELDS:
            raise HTTPException(
                status_code=400,
                detail=f"Camp de sortare necunoscut: {sort_field}. Valori valide: {', '.join(_ALL_FILTER_SORT_FIELDS)}.",
            )
    wants_financial_sort = sort_field in _FILTER_SORT_FINANCIAL_FIELDS

    if (wants_financial_filter or wants_financial_sort) and an is None:
        raise HTTPException(
            status_code=400,
            detail="Filtrarea sau sortarea dupa campuri financiare necesita parametrul `an`.",
        )

    # Principal CAEN code via a correlated subquery rather than a JOIN, so firms with
    # multiple CAEN codes don't produce duplicate rows in the result set.
    caen_principal_expr = (
        select(CompanyCaenCode.caen_code)
        .where(CompanyCaenCode.company_id == Company.id, CompanyCaenCode.is_principal.is_(True))
        .limit(1)
        .scalar_subquery()
    )

    stmt = select(Company, caen_principal_expr.label("caen_principal"))

    if judet:
        stmt = stmt.where(Company.county == judet)
    if localitate:
        stmt = stmt.where(Company.locality == localitate)
    if forma_juridica:
        stmt = stmt.where(Company.legal_form == forma_juridica)
    if are_coordonate is True:
        stmt = stmt.where(Company.latitude.isnot(None), Company.longitude.isnot(None))
    elif are_coordonate is False:
        stmt = stmt.where(or_(Company.latitude.is_(None), Company.longitude.is_(None)))
    if caen:
        stmt = stmt.where(
            select(literal(1))
            .select_from(CompanyCaenCode)
            .where(CompanyCaenCode.company_id == Company.id, CompanyCaenCode.caen_code == caen)
            .exists()
        )

    if an is not None:
        # LEFT JOIN: firms without a company_financials row for `an` still appear
        # (financiar=None) unless a min/max threshold excludes them -- comparing a
        # NULL column is never true, so such firms are naturally dropped by filters.
        stmt = stmt.add_columns(CompanyFinancial).outerjoin(
            CompanyFinancial,
            and_(CompanyFinancial.company_id == Company.id, CompanyFinancial.an == an),
        )
        for field_name, (min_value, max_value) in financial_filters.items():
            column = _FILTER_SORT_FINANCIAL_FIELDS[field_name]
            if min_value is not None:
                stmt = stmt.where(column >= min_value)
            if max_value is not None:
                stmt = stmt.where(column <= max_value)

    # Count matches before order_by/offset/limit are applied, for pagination.
    total = session.scalar(select(func.count()).select_from(stmt.subquery()))

    if sort_field in _FILTER_SORT_FINANCIAL_FIELDS:
        column = _FILTER_SORT_FINANCIAL_FIELDS[sort_field]
        order_expr = (desc(column) if descending else column).nulls_last()
    elif sort_field in _FILTER_SORT_COMPANY_FIELDS:
        column = _FILTER_SORT_COMPANY_FIELDS[sort_field]
        order_expr = desc(column) if descending else column
    else:
        order_expr = Company.normalized_name

    rows = session.execute(stmt.order_by(order_expr).offset(offset).limit(limit)).all()

    results = []
    for row in rows:
        if an is not None:
            company, caen_principal, financial = row
        else:
            company, caen_principal = row
            financial = None

        results.append(
            CompanyFilterItem(
                cui=company.cui,
                name=company.name,
                county=company.county,
                locality=company.locality,
                legal_form=company.legal_form,
                caen_principal=caen_principal,
                are_coordonate=company.latitude is not None and company.longitude is not None,
                financiar=(
                    CompanyFilterFinancialSnapshot(
                        an=an,
                        cifra_afaceri=financial.cifra_afaceri,
                        profit_net=financial.profit_net,
                        numar_salariati=financial.numar_salariati,
                        datorii=financial.datorii,
                        active_circulante_total=financial.active_circulante_total,
                    )
                    if financial is not None
                    else None
                ),
            )
        )

    return CompanyFilterResponse(total=total, limit=limit, offset=offset, results=results)


@router.get(
    "/{cui}/bilant",
    response_model=BilantResponse,
    summary="Bilant financiar (ANAF) al unei firme",
    description=(
        "Situatii financiare (bilant) preluate de la webservice-ul public ANAF, pentru unul sau mai "
        "multi ani fiscali, interogati in paralel. Implicit: ultimul an fiscal incheiat "
        "(`anul curent - 1`). Maxim 5 ani per cerere. Raspunsul include `name`, `caen_code`, "
        "`caen_label` si o lista `years` cu indicatorii financiari standardizati (I1-I20) pentru "
        "fiecare an. `warning` este populat cand se solicita mai mult de un an."
    ),
)
@limiter.limit(_dynamic_limit)
async def get_company_bilant(
    request: Request,
    cui: int = Path(..., ge=1, description="Cod unic de identificare"),
    ani: list[int] | None = Query(
        default=None,
        description="Ani fiscali (ex: ?ani=2022&ani=2023). Implicit: ultimul an fiscal.",
    ),
):
    if not ani:
        ani = [date.today().year - 1]  # last closed fiscal year

    ani = sorted(set(ani), reverse=True)  # dedupe requested years, newest first

    if len(ani) > 5:
        raise HTTPException(status_code=400, detail="Maxim 5 ani pot fi interogati simultan.")

    warning = (
        f"Se interogheaza {len(ani)} ani fiscali de la ANAF in paralel. Raspunsul poate dura mai mult."
        if len(ani) > 1
        else None
    )

    # Fetch every requested year concurrently rather than sequentially -- each is an
    # independent ANAF call, so this bounds the request latency to the slowest year.
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_fetch_bilant_year(client, cui, year) for year in ani]
        )

    company_name = None
    caen_code = None
    caen_label = None
    years_data: list[BilantYear] = []

    for year, data in zip(ani, results):
        if data is None:
            continue
        if company_name is None:
            company_name = data.get("deni")
            caen_code = data.get("caen")
            caen_label = data.get("den_caen")
        years_data.append(
            BilantYear(
                year=year,
                indicators=[
                    BilantIndicator(
                        label=i["val_den_indicator"].strip(),
                        value=i["val_indicator"],
                    )
                    for i in data["i"]
                ],
            )
        )

    if not years_data:
        raise HTTPException(
            status_code=404,
            detail=f"Nu exista date bilant pentru CUI {cui} in anii solicitati.",
        )

    return BilantResponse(
        cui=cui,
        name=company_name or "",
        caen_code=caen_code or 0,
        caen_label=caen_label or "",
        years=years_data,
        warning=warning,
    )


_BILANT_MIN_YEAR = 2014


@router.get(
    "/{cui}/bilant/ultimul-an",
    response_model=BilantResponse,
    summary="Ultimul an fiscal cu bilant disponibil al unei firme",
    description=(
        "Cauta, incepand cu ultimul an fiscal incheiat (`anul curent - 1`) si mergand inapoi in timp "
        "an cu an, primul an pentru care ANAF are date de bilant disponibile pentru firma respectiva. "
        "Util pentru firme inchise/radiate, la care ultimii ani nu mai au bilant depus -- de exemplu, "
        "daca firma a fost radiata in 2013, se returneaza bilantul din 2012. Cautarea se opreste la "
        f"primul an gasit sau la anul {_BILANT_MIN_YEAR} (limita inferioara de date ANAF), caz in care "
        "raspunde cu 404."
    ),
)
@limiter.limit(_dynamic_limit)
async def get_company_bilant_ultimul_an(
    request: Request,
    cui: int = Path(..., ge=1, description="Cod unic de identificare"),
):
    an = date.today().year - 1
    data = None

    # Walk backwards year by year (sequentially, not in parallel like /bilant) since
    # we stop at the first hit -- most companies have last year's data, so this is
    # usually a single request; only closed/deregistered companies pay for more.
    async with httpx.AsyncClient() as client:
        while an >= _BILANT_MIN_YEAR:
            data = await _fetch_bilant_year(client, cui, an)
            if data is not None:
                break
            an -= 1

    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"Nu exista niciun bilant disponibil pentru CUI {cui}.",
        )

    return BilantResponse(
        cui=cui,
        name=data.get("deni") or "",
        caen_code=data.get("caen") or 0,
        caen_label=data.get("den_caen") or "",
        years=[
            BilantYear(
                year=an,
                indicators=[
                    BilantIndicator(label=i["val_den_indicator"].strip(), value=i["val_indicator"])
                    for i in data["i"]
                ],
            )
        ],
    )


@router.get(
    "/{cui}/caen",
    response_model=CompanyCaenResponse,
    summary="Coduri CAEN ale unei firme",
    description=(
        "Codul CAEN principal si eventualele coduri secundare ale unei firme, identificata dupa CUI. "
        "Randurile sunt ordonate cu codul principal primul, apoi dupa cod. Raspunde cu 404 daca firma "
        "nu exista sau nu are coduri CAEN inregistrate."
    ),
)
@limiter.limit(_dynamic_limit)
def get_company_caen(
    request: Request,
    cui: int = Path(..., ge=1, description="Cod unic de identificare"),
    session: Session = Depends(get_company_session),
):
    company = session.scalar(select(Company).where(Company.cui == cui))
    if company is None:
        raise HTTPException(status_code=404, detail=f"Compania cu CUI {cui} nu a fost gasita.")

    rows = session.scalars(
        select(CompanyCaenCode)
        .where(CompanyCaenCode.company_id == company.id)
        .order_by(desc(CompanyCaenCode.is_principal), CompanyCaenCode.caen_code)
    ).all()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"Nu exista coduri CAEN pentru compania cu CUI {cui}.",
        )

    principal = next((row for row in rows if row.is_principal), None)  # at most one, enforced at import time
    secundare = [row for row in rows if not row.is_principal]

    return CompanyCaenResponse(
        cui=cui,
        principal=CompanyCaenItem.model_validate(principal) if principal else None,
        secundare=[CompanyCaenItem.model_validate(row) for row in secundare],
    )


_MAX_FINANCIAR_ANI = 20


@router.get(
    "/{cui}/financiar",
    response_model=CompanyFinancialsResponse,
    summary="Date financiare stocate ale unei firme",
    description=(
        "Indicatori financiari anuali importati in baza de date (tabela `company_financials`), "
        "distincti de `/bilant` (care interogheaza live webservice-ul ANAF). Implicit: doar ultimul "
        "an pentru care exista date. Anii pot fi selectati explicit cu `ani` (repetabil) sau cu un "
        "interval `an_start`/`an_end`; daca `ani` este prezent, `an_start`/`an_end` sunt ignorate. "
        "Campurile returnate in `values` pot fi restranse cu `campuri` (repetabil); implicit sunt "
        "returnate toate campurile financiare."
    ),
)
@limiter.limit(_dynamic_limit)
def get_company_financiar(
    request: Request,
    cui: int = Path(..., ge=1, description="Cod unic de identificare"),
    ani: list[int] | None = Query(
        default=None,
        description="Ani pentru care se cer date (ex: ?ani=2022&ani=2023). Implicit: ultimul an disponibil.",
    ),
    an_start: int | None = Query(default=None, description="Inceputul intervalului de ani (ignorat daca `ani` este dat)."),
    an_end: int | None = Query(default=None, description="Sfarsitul intervalului de ani (ignorat daca `ani` este dat)."),
    campuri: list[str] | None = Query(
        default=None,
        description=f"Campuri financiare de returnat (ex: ?campuri=cifra_afaceri). Implicit: toate ({', '.join(FINANCIAL_COLUMNS)}).",
    ),
    session: Session = Depends(get_company_session),
):
    """Read financial data from the local company_financials table (see route
    description above for the full param semantics)."""
    if campuri:
        invalid = [c for c in campuri if c not in FINANCIAL_COLUMNS]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Campuri necunoscute: {', '.join(invalid)}. Campuri valide: {', '.join(FINANCIAL_COLUMNS)}.",
            )
        selected_fields = list(dict.fromkeys(campuri))  # dedupe, keep caller's order
    else:
        selected_fields = list(FINANCIAL_COLUMNS)

    # `ani` takes priority over an_start/an_end when both are given (see route description).
    if ani:
        ani = sorted(set(ani), reverse=True)
        if len(ani) > _MAX_FINANCIAR_ANI:
            raise HTTPException(status_code=400, detail=f"Maxim {_MAX_FINANCIAR_ANI} ani pot fi interogati simultan.")
    elif an_start is not None or an_end is not None:
        if an_start is None or an_end is None:
            raise HTTPException(status_code=400, detail="`an_start` si `an_end` trebuie furnizate impreuna.")
        if an_start > an_end:
            raise HTTPException(status_code=400, detail="`an_start` nu poate fi mai mare decat `an_end`.")

    company = session.scalar(select(Company).where(Company.cui == cui))
    if company is None:
        raise HTTPException(status_code=404, detail=f"Compania cu CUI {cui} nu a fost gasita.")

    stmt = select(CompanyFinancial).where(CompanyFinancial.company_id == company.id)
    if ani:
        stmt = stmt.where(CompanyFinancial.an.in_(ani))
    elif an_start is not None and an_end is not None:
        stmt = stmt.where(CompanyFinancial.an.between(an_start, an_end))
    else:
        # No years requested: resolve the latest year with data for this company
        # rather than assuming "current year - 1" like /bilant does, since imported
        # financial data may lag behind or vary in coverage per company.
        latest_an = session.scalar(
            select(func.max(CompanyFinancial.an)).where(CompanyFinancial.company_id == company.id)
        )
        if latest_an is None:
            raise HTTPException(status_code=404, detail=f"Nu exista date financiare pentru CUI {cui}.")
        stmt = stmt.where(CompanyFinancial.an == latest_an)

    rows = session.scalars(stmt.order_by(desc(CompanyFinancial.an))).all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Nu exista date financiare pentru CUI {cui} in anii solicitati.")

    return CompanyFinancialsResponse(
        cui=cui,
        name=company.name,
        fields=selected_fields,
        years=[
            CompanyFinancialYear(
                an=row.an,
                sursa=row.sursa,
                caen=row.caen,
                values={field: getattr(row, field) for field in selected_fields},
            )
            for row in rows
        ],
    )


@router.get(
    "/{cui}/financiar/evolutie",
    response_model=CompanyFinancialSeriesResponse,
    summary="Evolutia in timp a unui singur indicator financiar",
    description=(
        "Seria de valori pentru un singur indicator financiar stocat (`camp`), an cu an, ordonata "
        "crescator -- utila pentru grafice. Implicit sunt returnati toti anii disponibili pentru "
        "firma; pot fi restransi cu `ani` (repetabil) sau cu un interval `an_start`/`an_end` (ignorat "
        "daca `ani` este dat)."
    ),
)
@limiter.limit(_dynamic_limit)
def get_company_financiar_evolutie(
    request: Request,
    cui: int = Path(..., ge=1, description="Cod unic de identificare"),
    camp: str = Query(
        ..., description=f"Indicatorul de urmarit. Valori valide: {', '.join(FINANCIAL_COLUMNS)}."
    ),
    ani: list[int] | None = Query(
        default=None,
        description="Ani pentru care se cere valoarea (ex: ?ani=2022&ani=2023). Implicit: toti anii disponibili.",
    ),
    an_start: int | None = Query(default=None, description="Inceputul intervalului de ani (ignorat daca `ani` este dat)."),
    an_end: int | None = Query(default=None, description="Sfarsitul intervalului de ani (ignorat daca `ani` este dat)."),
    session: Session = Depends(get_company_session),
):
    """Time-series read of a single stored financial field, for charting."""
    if camp not in FINANCIAL_COLUMNS:
        raise HTTPException(
            status_code=400,
            detail=f"Camp necunoscut: {camp}. Campuri valide: {', '.join(FINANCIAL_COLUMNS)}.",
        )

    if ani:
        ani = sorted(set(ani))  # ascending, unlike /financiar's descending -- this is a time series
        if len(ani) > _MAX_FINANCIAR_ANI:
            raise HTTPException(status_code=400, detail=f"Maxim {_MAX_FINANCIAR_ANI} ani pot fi interogati simultan.")
    elif an_start is not None or an_end is not None:
        if an_start is None or an_end is None:
            raise HTTPException(status_code=400, detail="`an_start` si `an_end` trebuie furnizate impreuna.")
        if an_start > an_end:
            raise HTTPException(status_code=400, detail="`an_start` nu poate fi mai mare decat `an_end`.")

    company = session.scalar(select(Company).where(Company.cui == cui))
    if company is None:
        raise HTTPException(status_code=404, detail=f"Compania cu CUI {cui} nu a fost gasita.")

    column = getattr(CompanyFinancial, camp)
    stmt = select(CompanyFinancial.an, column).where(CompanyFinancial.company_id == company.id)
    if ani:
        stmt = stmt.where(CompanyFinancial.an.in_(ani))
    elif an_start is not None and an_end is not None:
        stmt = stmt.where(CompanyFinancial.an.between(an_start, an_end))

    rows = session.execute(stmt.order_by(CompanyFinancial.an)).all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Nu exista date financiare pentru CUI {cui} in anii solicitati.")

    return CompanyFinancialSeriesResponse(
        cui=cui,
        name=company.name,
        camp=camp,
        puncte=[FinancialSeriesPoint(an=an, valoare=valoare) for an, valoare in rows],
    )


@router.get(
    "/financiar/clasament",
    response_model=FinancialLeaderboardResponse,
    summary="Clasament firme dupa un indicator financiar",
    description=(
        "Top firme dupa valoarea unui indicator financiar stocat (`camp`), pentru un an dat "
        "(`an`), in ordine descrescatoare. Firmele fara valoare pentru indicatorul cerut in anul "
        "respectiv sunt excluse din clasament. Optional filtrabil dupa `caen` (codul CAEN raportat "
        "de firma in anul respectiv) si/sau `county` (judet, potrivire exacta)."
    ),
)
@limiter.limit(_dynamic_limit)
def get_financiar_clasament(
    request: Request,
    an: int = Query(..., description="Anul fiscal de clasificat"),
    camp: str = Query(
        ..., description=f"Indicatorul de clasificare. Valori valide: {', '.join(FINANCIAL_COLUMNS)}."
    ),
    caen: str | None = Query(default=None, description="Filtreaza dupa codul CAEN raportat in anul respectiv."),
    county: str | None = Query(default=None, description="Filtreaza dupa judet (potrivire exacta)."),
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_company_session),
):
    """Cross-company ranking for one year/indicator -- unlike the other /financiar
    routes, this is a search-style query (no CUI, no 404 on an empty result)."""
    if camp not in FINANCIAL_COLUMNS:
        raise HTTPException(
            status_code=400,
            detail=f"Camp necunoscut: {camp}. Campuri valide: {', '.join(FINANCIAL_COLUMNS)}.",
        )

    column = getattr(CompanyFinancial, camp)
    stmt = (
        select(Company.cui, Company.name, Company.county, CompanyFinancial.caen, column)
        .join(Company, Company.id == CompanyFinancial.company_id)
        .where(CompanyFinancial.an == an, column.isnot(None))
    )
    if caen:
        stmt = stmt.where(CompanyFinancial.caen == caen)
    if county:
        stmt = stmt.where(Company.county == county)

    rows = session.execute(stmt.order_by(desc(column)).limit(limit)).all()

    return FinancialLeaderboardResponse(
        an=an,
        camp=camp,
        results=[
            FinancialLeaderboardItem(cui=cui, name=name, county=county_row, caen=caen_row, valoare=valoare)
            for cui, name, county_row, caen_row, valoare in rows
        ],
    )


def _python_median(values: list[int]) -> float | None:
    """Median for dialects without a percentile_cont aggregate (SQLite, used by tests)."""
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return float(values[mid])
    return (values[mid - 1] + values[mid]) / 2.0


@router.get(
    "/financiar/statistici",
    response_model=FinancialStatsResponse,
    summary="Statistici agregate pentru un grup de firme",
    description=(
        "Statistici agregate (numar de firme, suma, medie, mediana, minim, maxim) pentru un "
        "indicator financiar stocat (`camp`), intr-un an dat (`an`), pe un grup de firme filtrat "
        "optional dupa `judet`, `localitate` si/sau `caen` (codul CAEN raportat de firma in anul "
        "respectiv, la fel ca la `/financiar/clasament` -- nu codurile principal/secundar din "
        "`company_caen_codes`). Firmele fara valoare pentru indicatorul cerut in anul respectiv "
        "sunt excluse din calcul. Util pentru context de piata (ex: cifra de afaceri medie pentru "
        "un CAEN intr-un judet), spre deosebire de `/clasament` (firme individuale) sau "
        "`/comparatie` (firme alese explicit). Cererile fara `localitate` si fara `judet`+`caen` "
        "combinate (adica national, doar `judet`, sau doar `caen`) raspund instant din statistici "
        "precalculate offline (`sursa=\"precalculat\"`; `mediana` este null in acest caz). Restul "
        "combinatiilor de filtre calculeaza live (`sursa=\"live\"`), inclusiv mediana exacta."
    ),
)
@limiter.limit(_dynamic_limit)
def get_financiar_statistici(
    request: Request,
    an: int = Query(..., description="Anul fiscal de agregat"),
    camp: str = Query(
        ..., description=f"Indicatorul de agregat. Valori valide: {', '.join(FINANCIAL_COLUMNS)}."
    ),
    judet: str | None = Query(default=None, description="Filtreaza dupa judet (potrivire exacta)."),
    localitate: str | None = Query(default=None, description="Filtreaza dupa localitate (potrivire exacta)."),
    caen: str | None = Query(default=None, description="Filtreaza dupa codul CAEN raportat in anul respectiv."),
    session: Session = Depends(get_company_session),
):
    """Aggregate-only endpoint -- unlike /clasament, doesn't return individual companies."""
    if camp not in FINANCIAL_COLUMNS:
        raise HTTPException(
            status_code=400,
            detail=f"Camp necunoscut: {camp}. Campuri valide: {', '.join(FINANCIAL_COLUMNS)}.",
        )

    # National / judet-only / caen-only can be served instantly from CompanyFinancialStats
    # (refreshed offline by scripts/refresh_company_financial_stats.py); localitate and the
    # judet+caen combination aren't precomputed (see that script's module docstring) and
    # always fall through to the live query below.
    if not localitate and not (judet and caen):
        national_row_exists = (
            session.scalar(
                select(CompanyFinancialStats.id).where(
                    CompanyFinancialStats.an == an,
                    CompanyFinancialStats.camp == camp,
                    CompanyFinancialStats.judet.is_(None),
                    CompanyFinancialStats.caen.is_(None),
                )
            )
            is not None
        )
        if national_row_exists:
            # The table has been refreshed for this (an, camp); trust it completely for
            # this granularity, including "no row" meaning zero matching companies.
            stats_row = session.scalar(
                select(CompanyFinancialStats).where(
                    CompanyFinancialStats.an == an,
                    CompanyFinancialStats.camp == camp,
                    CompanyFinancialStats.judet == judet,
                    CompanyFinancialStats.caen == caen,
                )
            )
            return FinancialStatsResponse(
                an=an,
                camp=camp,
                judet=judet,
                localitate=localitate,
                caen=caen,
                numar_firme=stats_row.numar_firme if stats_row else 0,
                suma=stats_row.suma if stats_row else None,
                medie=stats_row.medie if stats_row else None,
                mediana=stats_row.mediana if stats_row else None,
                minim=stats_row.minim if stats_row else None,
                maxim=stats_row.maxim if stats_row else None,
                sursa="precalculat",
            )
        # No row at all for this (an, camp) -- table not refreshed (yet) for it; fall
        # through to the live query so the endpoint still works correctly either way.

    column = getattr(CompanyFinancial, camp)
    conditions = [CompanyFinancial.an == an, column.isnot(None)]
    if judet:
        conditions.append(Company.county == judet)
    if localitate:
        conditions.append(Company.locality == localitate)
    if caen:
        conditions.append(CompanyFinancial.caen == caen)

    # Only join `companies` when actually filtering by judet/localitate -- `caen` and `an`
    # live on CompanyFinancial directly, and the join is expensive at this table's scale
    # (4M+ rows) for no benefit when no location filter is requested.
    needs_company_join = bool(judet or localitate)

    def _stats_query(*select_columns):
        stmt = select(*select_columns).select_from(CompanyFinancial)
        if needs_company_join:
            stmt = stmt.join(Company, Company.id == CompanyFinancial.company_id)
        return stmt.where(*conditions)

    count, suma, medie, minim, maxim = session.execute(
        _stats_query(func.count(column), func.sum(column), func.avg(column), func.min(column), func.max(column))
    ).one()

    # Median has no portable SQL aggregate across our two dialects (SQLite, used by the
    # test suite, has no percentile_cont) -- use Postgres's native aggregate in production
    # and fall back to computing it in Python for SQLite.
    if session.bind and session.bind.dialect.name == "postgresql":
        mediana = session.scalar(_stats_query(func.percentile_cont(0.5).within_group(column)))
        mediana = float(mediana) if mediana is not None else None
    else:
        values = sorted(session.scalars(_stats_query(column)).all())
        mediana = _python_median(values)

    return FinancialStatsResponse(
        an=an,
        camp=camp,
        judet=judet,
        localitate=localitate,
        caen=caen,
        numar_firme=count,
        suma=suma,
        medie=float(medie) if medie is not None else None,
        mediana=mediana,
        minim=minim,
        maxim=maxim,
        sursa="live",
    )


@router.get(
    "/{cui}/financiar/indicatori",
    response_model=CompanyFinancialIndicatorsResponse,
    summary="Indicatori financiari derivati (marja, crestere, cifra de afaceri per salariat)",
    description=(
        "Rate calculate din campurile stocate in company_financials, nu date brute ANAF/MFP: "
        "`marja_profit` (profit_net / cifra_afaceri), `cifra_afaceri_per_salariat` "
        "(cifra_afaceri / numar_salariati) si cresterile an-peste-an ale cifrei de afaceri si "
        "profitului net. Cresterile se calculeaza fata de anul anterior din acest raspuns, nu "
        "neaparat anul calendaristic precedent -- daca lipseste un an intermediar, cresterea se "
        "raporteaza totusi fata de cel mai apropiat an anterior returnat. Un indicator este null "
        "cand oricare valoare implicata lipseste sau numitorul este zero. Implicit sunt returnati "
        "toti anii disponibili; pot fi restransi cu `ani` sau cu un interval `an_start`/`an_end`."
    ),
)
@limiter.limit(_dynamic_limit)
def get_company_financiar_indicatori(
    request: Request,
    cui: int = Path(..., ge=1, description="Cod unic de identificare"),
    ani: list[int] | None = Query(
        default=None,
        description="Ani pentru care se calculeaza indicatorii (ex: ?ani=2022&ani=2023). Implicit: toti anii disponibili.",
    ),
    an_start: int | None = Query(default=None, description="Inceputul intervalului de ani (ignorat daca `ani` este dat)."),
    an_end: int | None = Query(default=None, description="Sfarsitul intervalului de ani (ignorat daca `ani` este dat)."),
    session: Session = Depends(get_company_session),
):
    """Compute per-year ratios from stored fields -- nothing here is persisted."""
    if ani:
        ani = sorted(set(ani))  # ascending: growth needs chronological order
        if len(ani) > _MAX_FINANCIAR_ANI:
            raise HTTPException(status_code=400, detail=f"Maxim {_MAX_FINANCIAR_ANI} ani pot fi interogati simultan.")
    elif an_start is not None or an_end is not None:
        if an_start is None or an_end is None:
            raise HTTPException(status_code=400, detail="`an_start` si `an_end` trebuie furnizate impreuna.")
        if an_start > an_end:
            raise HTTPException(status_code=400, detail="`an_start` nu poate fi mai mare decat `an_end`.")

    company = session.scalar(select(Company).where(Company.cui == cui))
    if company is None:
        raise HTTPException(status_code=404, detail=f"Compania cu CUI {cui} nu a fost gasita.")

    stmt = select(
        CompanyFinancial.an,
        CompanyFinancial.cifra_afaceri,
        CompanyFinancial.profit_net,
        CompanyFinancial.numar_salariati,
    ).where(CompanyFinancial.company_id == company.id)
    if ani:
        stmt = stmt.where(CompanyFinancial.an.in_(ani))
    elif an_start is not None and an_end is not None:
        stmt = stmt.where(CompanyFinancial.an.between(an_start, an_end))

    rows = session.execute(stmt.order_by(CompanyFinancial.an)).all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Nu exista date financiare pentru CUI {cui} in anii solicitati.")

    def _ratio(numerator: int | None, denominator: int | None) -> float | None:
        if numerator is None or denominator is None or denominator == 0:
            return None
        return numerator / denominator

    def _growth(current: int | None, previous: int | None) -> float | None:
        if current is None or previous is None or previous == 0:
            return None
        return (current - previous) / previous

    years: list[FinancialIndicatorYear] = []
    prev_cifra: int | None = None
    prev_profit: int | None = None
    for an, cifra, profit, salariati in rows:
        years.append(
            FinancialIndicatorYear(
                an=an,
                marja_profit=_ratio(profit, cifra),
                cifra_afaceri_per_salariat=_ratio(cifra, salariati),
                crestere_cifra_afaceri=_growth(cifra, prev_cifra),
                crestere_profit_net=_growth(profit, prev_profit),
            )
        )
        prev_cifra, prev_profit = cifra, profit

    return CompanyFinancialIndicatorsResponse(cui=cui, name=company.name, years=years)


_MAX_COMPARATIE_CUI = 20


@router.get(
    "/comparatie",
    response_model=CompanyComparisonResponse,
    summary="Comparatie financiara intre mai multe firme",
    description=(
        "Compara intre 2 si "
        f"{_MAX_COMPARATIE_CUI} firme (identificate prin `cui`, repetabil) intr-o structura comuna: "
        "cifra de afaceri, profit net, salariati, active circulante, datorii, marja de profit, "
        "cifra de afaceri per salariat si cresterea an-peste-an a cifrei de afaceri/profitului. "
        "Implicit fiecare firma foloseste propriul ultim an disponibil (poate diferi intre firme); "
        "un `an` explicit forteaza acelasi an fiscal pentru toate. CUI-urile care nu corespund unei "
        "firme sunt raportate separat in `cui_negasite`, fara sa opreasca restul comparatiei."
    ),
)
@limiter.limit(_dynamic_limit)
def get_companii_comparatie(
    request: Request,
    cui: list[int] = Query(
        ..., description="CUI-urile de comparat (ex: ?cui=123&cui=456&cui=789). Minim 2, maxim "
        f"{_MAX_COMPARATIE_CUI}."
    ),
    an: int | None = Query(
        default=None,
        description="Anul fiscal de comparat. Implicit: ultimul an disponibil, calculat separat pentru fiecare firma.",
    ),
    session: Session = Depends(get_company_session),
):
    cuis = list(dict.fromkeys(cui))  # dedupe, keep caller's order
    if len(cuis) < 2:
        raise HTTPException(status_code=400, detail="Sunt necesare minim 2 CUI-uri pentru comparatie.")
    if len(cuis) > _MAX_COMPARATIE_CUI:
        raise HTTPException(status_code=400, detail=f"Maxim {_MAX_COMPARATIE_CUI} CUI-uri pot fi comparate simultan.")

    companies_by_cui = {
        c.cui: c for c in session.scalars(select(Company).where(Company.cui.in_(cuis))).all()
    }
    cui_negasite = [c for c in cuis if c not in companies_by_cui]

    def _ratio(numerator: int | None, denominator: int | None) -> float | None:
        if numerator is None or denominator is None or denominator == 0:
            return None
        return numerator / denominator

    def _growth(current: int | None, previous: int | None) -> float | None:
        if current is None or previous is None or previous == 0:
            return None
        return (current - previous) / previous

    # One pair of queries per company (current year + nearest earlier year, for growth)
    # rather than a single batched query: each company can resolve to a different
    # "latest year" when `an` isn't given, which a single WHERE an=... can't express.
    # Fine at this scale -- the endpoint caps input at _MAX_COMPARATIE_CUI companies.
    results: list[CompanyComparisonItem] = []
    for company_cui in cuis:
        company = companies_by_cui.get(company_cui)
        if company is None:
            continue

        target_an = an
        if target_an is None:
            target_an = session.scalar(
                select(func.max(CompanyFinancial.an)).where(CompanyFinancial.company_id == company.id)
            )

        current = previous = None
        if target_an is not None:
            current = session.scalar(
                select(CompanyFinancial).where(
                    CompanyFinancial.company_id == company.id, CompanyFinancial.an == target_an
                )
            )
            previous = session.scalar(
                select(CompanyFinancial)
                .where(CompanyFinancial.company_id == company.id, CompanyFinancial.an < target_an)
                .order_by(desc(CompanyFinancial.an))
                .limit(1)
            )

        cifra = current.cifra_afaceri if current else None
        profit = current.profit_net if current else None
        salariati = current.numar_salariati if current else None

        results.append(
            CompanyComparisonItem(
                cui=company.cui,
                name=company.name,
                an=current.an if current else None,
                cifra_afaceri=cifra,
                profit_net=profit,
                numar_salariati=salariati,
                active_circulante_total=current.active_circulante_total if current else None,
                datorii=current.datorii if current else None,
                marja_profit=_ratio(profit, cifra),
                cifra_afaceri_per_salariat=_ratio(cifra, salariati),
                crestere_cifra_afaceri=_growth(cifra, previous.cifra_afaceri if previous else None),
                crestere_profit_net=_growth(profit, previous.profit_net if previous else None),
            )
        )

    return CompanyComparisonResponse(an=an, cui_negasite=cui_negasite, results=results)


# --- NOU: GET /companii/{cui}/coordonate --------------------------------------
@router.get(
    "/{cui}/coordonate",
    response_model=CompanyCoordonateResponse,
    summary="Coordonate (latitudine/longitudine) ale unei firme",
    description=(
        "Daca firma are deja `latitude`/`longitude` populate in baza de date (de rularea bulk "
        "scripts/geocode_companies.py), returneaza instant valorile stocate (`sursa='stocat'`), "
        "fara niciun apel extern. Doar daca aceste coloane lipsesc, geocodifica adresa firmei pe "
        "loc via ArcGIS (`sursa='live'`). Endpoint strict read-only: rezultatul unei geocodari "
        "live NU este scris in baza de date -- apeluri repetate pentru o firma inca "
        "negeocodificata vor geocodifica din nou de fiecare data."
    ),
)
@limiter.limit(_dynamic_limit)
def get_company_coordonate(
    request: Request,
    cui: int = Path(..., ge=1, description="Cod unic de identificare"),
    session: Session = Depends(get_company_session),
    provider: GeocodingProvider = Depends(get_geocoding_provider),
):
    company = session.scalar(select(Company).where(Company.cui == cui))
    if company is None:
        raise HTTPException(status_code=404, detail=f"Compania cu CUI {cui} nu a fost gasita.")

    # NOU: cale rapida -- verifica direct coloanele latitude/longitude din tabela companies
    # (populate de rularea bulk scripts/geocode_companies.py); doar daca lipsesc se cade pe
    # geocodare live ArcGIS mai jos.
    if company.latitude is not None and company.longitude is not None:
        adresa = build_company_address(company) or ""
        return CompanyCoordonateResponse(
            cui=cui,
            latitude=company.latitude,
            longitude=company.longitude,
            score=company.geocode_score,
            sursa="stocat",
            adresa_folosita=adresa,
        )

    # NOU: firma inca negeocodificata (sau geocodare anterioara esuata) -- geocodifica pe loc.
    adresa = build_company_address(company)
    if adresa is None:
        raise HTTPException(
            status_code=404,
            detail=f"Adresa insuficienta pentru geocodare (CUI {cui}).",
        )

    try:
        result = provider.geocode(adresa)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Serviciul de geocodare este indisponibil momentan: {exc}",
        )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Nu s-a putut geocodifica adresa firmei cu CUI {cui}.",
        )

    return CompanyCoordonateResponse(
        cui=cui,
        latitude=result.lat,
        longitude=result.lon,
        score=result.score,
        sursa="live",
        adresa_folosita=adresa,
    )


@router.get(
    "/{cui}",
    response_model=CompanyOut,
    summary="Detalii complete firma dupa CUI",
    description=(
        "Inregistrarea completa a firmei dupa CUI, inclusiv adresa, forma juridica, detalii de "
        "inregistrare si toate campurile stocate."
    ),
)
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