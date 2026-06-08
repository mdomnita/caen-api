"""
API Romanian CAEN Codes – FastAPI + SQLite
"""
import time

from fastapi import FastAPI, Request, Response, Security
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from auth import limiter, _dynamic_limit, get_api_key, ensure_observability_tables, log_api_request
from routers import caen, ierarhie, siruta, schimb, zilelibere

app = FastAPI(
    title="Romanian CAEN Codes API",
    description=(
        "Cautare si navigare ierarhica a codurilor CAEN Rev. 3.\n\n"
        "**Ierarhie:** Sectiuni → Diviziuni → Grupe → Clase\n\n"
        "- `/sectiuni` — toate sectiunile\n"
        "- `/sectiuni/{cod}/diviziuni` — diviziunile unei sectiuni\n"
        "- `/diviziuni/{cod}/grupe` — grupele unei diviziuni\n"
        "- `/grupe/{cod}/clase` — clasele unei grupe (cu detalii complete)\n"
        "- `/caen/{cod}` — lookup direct dupa cod clasa (2-4 cifre)\n"
        "- `/caen?q=...` — cautare full-text in cod sau denumire"
    ),
    version="1.0.0",
    root_path="/api",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    dependencies=[Security(get_api_key)],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "0"
        return response


app.add_middleware(SecurityHeadersMiddleware)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        started_at = time.perf_counter()
        status_code = 500
        response = None

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
            try:
                log_api_request(request, status_code, duration_ms)
            except Exception:
                # Observability should not take the API down.
                pass


app.add_middleware(RequestLoggingMiddleware)


def _initialize_runtime_tables() -> None:
    ensure_observability_tables()


app.router.add_event_handler("startup", _initialize_runtime_tables)

app.include_router(caen.router)
app.include_router(ierarhie.router)
app.include_router(siruta.router)
app.include_router(schimb.router)
app.include_router(zilelibere.router)


@app.get("/", include_in_schema=False)
@limiter.limit(_dynamic_limit)
def root(request: Request):
    return RedirectResponse(url="/api/docs")


@app.get("/health", include_in_schema=False)
@limiter.limit(_dynamic_limit)
def health(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok"}
