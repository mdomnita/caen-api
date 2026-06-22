# Romanian Reference Data & Companies API

REST API for:

- CAEN Rev. 3 codes
- SIRUTA locality codes
- BNR exchange rates
- legal holidays
- company search by name and CUI

The application is unified under one FastAPI app: `main:app`.

## Data storage model

- SQLite:
  - CAEN
  - SIRUTA
  - exchange rates
  - legal holidays
  - observability tables
- PostgreSQL:
  - company dataset (`/companii` endpoints)

DB selection is done with FastAPI dependencies in `api_dependencies.py`, based on API section.

## Project structure

```text
.
├── main.py
├── api_dependencies.py
├── auth.py
├── init_db.py
├── routers/
│   ├── caen.py
│   ├── ierarhie.py
│   ├── siruta.py
│   ├── schimb.py
│   ├── zilelibere.py
│   ├── companies.py            # /companii endpoints
│   ├── company_database.py     # PostgreSQL engine, session factory, init_postgres()
│   ├── company_models.py       # SQLAlchemy Company model and index definitions
│   ├── company_schemas.py      # Pydantic response schemas
│   ├── company_utils.py        # normalize_company_name(), date/CUI parsing
│   └── company_main.py         # compatibility shim -> imports app from main
├── scripts/
│   ├── init_caen_db.py
│   ├── init_siruta_db.py
│   ├── init_exchange_db.py
│   ├── update_exchange_db.py
│   └── import_companies.py
├── Dockerfile
└── docker-compose.yml
```

## Authentication and rate limiting

All endpoints accept optional `X-API-KEY`.

| Scenario | Rate limit |
|---|---|
| No key | 10 requests / minute per IP |
| Valid `X-API-KEY` | 1000 requests / minute |
| Invalid `X-API-KEY` | 403 Forbidden |

## Root path behavior (`/api`)

The app is configured with `root_path="/api"`.

Important:

- if you run Uvicorn directly on localhost (no reverse proxy mount), call routes without `/api`
- if your reverse proxy mounts the app under `/api`, call routes with `/api`

Examples:

- local direct: `/companii/search?q=dacia`
- mounted under `/api`: `/api/companii/search?q=dacia`

## API endpoints

### CAEN and hierarchy (SQLite)

- `GET /caen/{cod}`
- `GET /caen?q={text}`
- `GET /sectiuni`
- `GET /sectiuni/{cod}`
- `GET /sectiuni/{cod}/diviziuni`
- `GET /diviziuni/{cod}`
- `GET /diviziuni/{cod}/grupe`
- `GET /grupe/{cod}`
- `GET /grupe/{cod}/clase`

### SIRUTA (SQLite)

- `GET /siruta/judete`
- `GET /siruta/localitate/{cod}`
- `GET /siruta/cautare?q={text}`
- `GET /siruta/judet/{cod_judet}`

### Exchange rates (SQLite)

- `GET /schimb/valute`
- `GET /schimb/valute/{data}`
- `GET /schimb/curs/{valuta}/{data}`
- `GET /schimb/evolutie/{valuta}?start=&end=`
- `GET /schimb/pereche/{sursa}/{destinatie}/{data}`
- `GET /schimb/evolutie/pereche/{sursa}/{destinatie}?start=&end=`

### Legal holidays (SQLite)

- `GET /zilelibere`
- `GET /zilelibere/luna/{luna}`
- `GET /zilelibere/punti`

### Companies (PostgreSQL)

- `GET /companii/search?q={text}&limit={1-50}` — fuzzy name search using prefix match + trigram
  similarity (`pg_trgm`). Returns lightweight fields only: `name`, `cui`, `county`, `locality`,
  `similarity`. Results are ordered by prefix rank then similarity score descending. `total` in
  the response reflects the number of rows returned, not the full DB match count.
- `GET /companii/autocomplete?q={text}&limit={1-20}` — prefix-only lookup (B-tree index, no
  trigram). Returns `name` and `cui`, ordered alphabetically by normalized name. Fastest option
  for type-ahead UIs.
- `GET /companii/{cui}` — full company record by CUI, including address, legal form, registration
  details, and all stored fields.
- `GET /companii/{cui}/bilant?ani=2022&ani=2023` — financial statements (bilant) from ANAF for
  one or more fiscal years. Years are fetched in parallel from the ANAF public webservice. Default:
  last fiscal year (`current_year - 1`). Maximum 5 years per request. Response includes `name`,
  `caen_code`, `caen_label`, and a `years` list each containing 20 standardised financial
  indicators (I1–I20). A `warning` field is populated when more than one year is requested.

## Local run

1) Create environment and install deps:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

2) Initialize SQLite datasets:

```bash
python init_db.py
```

3) Set PostgreSQL URL for companies:

```powershell
$env:DATABASE_URL="postgresql+psycopg://companies:Company_password@localhost:5435/companies"
```

4) Optional companies import:

```powershell
python scripts/import_companies.py --file .\temp\od_firme.csv --truncate
```

5) Start API:

```bash
uvicorn main:app --reload
```

Swagger UI: http://localhost:8000/docs

## Docker

Start all services:

```bash
docker compose up -d --build
```

Compose topology:

- `api` container: FastAPI (`main:app`)
- `db` container: PostgreSQL for companies
- `postgres_data` named volume: persistent PostgreSQL data

Import companies with tools profile:

```bash
docker compose --profile tools run --rm importer
```

This keeps company data persistent across restarts (no full reload each run).
