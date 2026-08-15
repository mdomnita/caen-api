import asyncio
from datetime import date

import httpx
import requests
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy import desc, func, literal, select
from sqlalchemy.orm import Session

from auth import limiter, _dynamic_limit
from api_dependencies import get_company_session
from routers.company_models import FINANCIAL_COLUMNS, Company, CompanyCaenCode, CompanyFinancial
from routers.company_schemas import (
    AutocompleteItem,
    AutocompleteResponse,
    BilantIndicator,
    BilantResponse,
    BilantYear,
    CompanyCaenItem,
    CompanyCaenResponse,
    CompanyCoordonateResponse,
    CompanyFinancialsResponse,
    CompanyFinancialYear,
    CompanyOut,
    CompanySearchItem,
    CompanySearchResponse,
)
from routers.company_utils import build_company_address, normalize_company_name
# NOU: pentru GET /companii/{cui}/coordonate
from services.geocoding import GeocodingProvider, get_geocoding_provider

_ANAF_BILANT_URL = "https://webservicesp.anaf.ro/bilant"


async def _fetch_bilant_year(client: httpx.AsyncClient, cui: int, year: int) -> dict | None:
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
        ani = [date.today().year - 1]

    ani = sorted(set(ani), reverse=True)

    if len(ani) > 5:
        raise HTTPException(status_code=400, detail="Maxim 5 ani pot fi interogati simultan.")

    warning = (
        f"Se interogheaza {len(ani)} ani fiscali de la ANAF in paralel. Raspunsul poate dura mai mult."
        if len(ani) > 1
        else None
    )

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

    principal = next((row for row in rows if row.is_principal), None)
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
    if campuri:
        invalid = [c for c in campuri if c not in FINANCIAL_COLUMNS]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Campuri necunoscute: {', '.join(invalid)}. Campuri valide: {', '.join(FINANCIAL_COLUMNS)}.",
            )
        selected_fields = list(dict.fromkeys(campuri))
    else:
        selected_fields = list(FINANCIAL_COLUMNS)

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