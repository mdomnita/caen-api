from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from routers.companies import router as companies_router
from routers.company_database import init_postgres


app = FastAPI(
    title="Romanian Companies Search API",
    description="FastAPI + PostgreSQL pentru cautare firme dupa nume si CUI.",
    version="1.0.0",
)

app.include_router(companies_router)
app.router.add_event_handler("startup", init_postgres)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}