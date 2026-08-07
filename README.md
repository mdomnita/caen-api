# Romanian Reference Data & Companies API

REST API for:

- CAEN Rev. 3 codes
- SIRUTA locality codes
- Romanian postal codes and free-form address resolution
- BNR exchange rates
- legal holidays
- company search by name and CUI

The application is unified under one FastAPI app: `main:app`.

## Data storage model

- SQLite:
  - CAEN
  - SIRUTA
  - localitati geo (nume + coordonate, sursa PostGIS separata)
  - coduri postale
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
│   ├── localitati.py           # /localitati endpoints (nume + coordonate)
│   ├── schimb.py
│   ├── zilelibere.py
│   ├── coduripostale.py        # /coduripostale endpoints
│   ├── companies.py            # /companii endpoints
│   ├── company_database.py     # PostgreSQL engine, session factory, init_postgres()
│   ├── company_models.py       # SQLAlchemy Company model and index definitions
│   ├── company_schemas.py      # Pydantic response schemas
│   ├── company_utils.py        # normalize_company_name(), date/CUI parsing
│   └── company_main.py         # compatibility shim -> imports app from main
├── helpers/
│   └── text_normalization.py   # strip_diacritics(), normalize_search() shared helpers
├── services/
│   └── geocoding.py            # GeocodingProvider protocol + ArcGisProvider
├── scripts/
│   ├── init_caen_db.py
│   ├── init_siruta_db.py
│   ├── init_localitati_geo_db.py  # needs LOCALITIES_DATABASE_URL (PostGIS)
│   ├── init_exchange_db.py
│   ├── update_exchange_db.py
│   ├── init_zile_libere_db.py
│   ├── init_coduri_postale_db.py  # imports Posta Romana postal-code CSVs
│   ├── import_companies.py     # inserts only new companies (by CUI), skips existing ones
│   └── update_companies.py     # updates only already-existing companies (by CUI), skips new ones
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

- `GET /caen/{cod}` — detalii complete pentru un cod CAEN (2-4 cifre)
- `GET /caen?q={text}` — cautare dupa cod partial sau text din denumire
- `GET /caen/corespondenta?v2=&v3=&tip=&limit=&offset=` — corespondente CAEN Rev.2 <-> Rev.3;
  necesita cel putin unul dintre `v2`/`v3`
- `GET /caen/v2/{cod}` — detalii clasa CAEN Rev.2 si corespondentele ei catre Rev.3
- `GET /caen/v3/{cod}/v2` — codurile CAEN Rev.2 din care provine un cod Rev.3
- `GET /sectiuni`
- `GET /sectiuni/{cod}`
- `GET /sectiuni/{cod}/diviziuni`
- `GET /diviziuni/{cod}`
- `GET /diviziuni/{cod}/grupe`
- `GET /grupe/{cod}`
- `GET /grupe/{cod}/clase`

### SIRUTA (SQLite)

- `GET /siruta/judete`
- `GET /siruta/localitate/{cod}` — lookup dupa codul SIRUTA
- `GET /siruta/cautare?q={text}&limit=&offset=`
- `GET /siruta/judet/{cod_judet}?tip_cod=` — toate localitatile dintr-un judet; `tip_cod` optional
  filtreaza dupa tip de UAT (12=municipiu, 13=oras, 14=comuna, 16=sector)

### Localitati geo (SQLite)

Nume de localitati cu coordonate (centroid), derivate dintr-o sursa PostGIS separata
(`localitati_geo`) — util pentru harti; nu contine coduri SIRUTA (pentru acelea, vezi
`/siruta` de mai sus).

- `GET /localitati/search?q=&limit=&offset=` — cautare dupa nume (cu sau fara diacritice)
- `GET /localitati/localitate/{nume}?judet=` — lookup dupa nume exact; `judet` optional pentru
  dezambiguizare cand exista localitati omonime in judete diferite
- `GET /localitati/judet/{judet}` — toate localitatile dintr-un judet, dupa nume

### Postal codes (SQLite)

Source: [data.gov.ro coduri-postale-romania](https://data.gov.ro/dataset/coduri-postale-romania)
(Poșta Română, May 2016 — the field itself is `sursa_versiune` on each row).

- `GET /coduripostale/{cod}` — lookup by 6-digit postal code. Returns a **list**, since a
  postal code is not unique (it can cover multiple streets/number ranges).
- `GET /coduripostale/cautare?judet=&localitate=&strada=&numar=&numar_tip=&limit=&offset=` —
  combined filter search; at least one filter is required. `numar` matches parsed street-number
  (`nr.`) ranges by containment + parity, and exact numeric block (`bl.`) tokens by equality —
  a street can have both, so the same `numar` can match two different rows (a house-number
  range and an unrelated block). Use `numar_tip=nr|bl` to disambiguate when that matters.
  Letter-suffixed or otherwise unparseable numbers are not matched by `numar` (`numar_raw` is
  always available for display/substring search via `strada`).
- `GET /coduripostale/autocomplete?tip=judet|localitate|strada&q=&localitate=&limit=` —
  prefix search for type-ahead UIs; `tip=strada` requires `localitate` to scope results.
- `GET /coduripostale/rezolvare?adresa=` — resolves a free-form address. Tries a local
  județ/localitate/strada containment match first (own data, no restrictions); if nothing
  matches, falls back to the ArcGIS geocoding provider (`services/geocoding.py`) for an
  approximate lat/lon. Returns up to 5 ranked candidates tagged by `source`
  (`"local"` or `"provider:arcgis"`) rather than silently picking one for ambiguous input.
  Provider results are never persisted, only served via standard HTTP caching.

### Exchange rates (SQLite)

- `GET /schimb/valute` — ultimul curs disponibil pentru fiecare valuta, fata de RON
- `GET /schimb/valute/{data}` — cursurile valabile la o data (sau ultima zi lucratoare anterioara)
- `GET /schimb/curs/{valuta}/{data}` — cursul unei valute la o data (sau ultima zi anterioara)
- `GET /schimb/evolutie/{valuta}?start=&end=` — puncte de evolutie intr-o perioada (end implicit: azi)
- `GET /schimb/istoric/{valuta}?from=&to=` — ca mai sus, dar interval obligatoriu, raspuns doar
  cu lista de puncte (fara antet)
- `GET /schimb/pereche/{sursa}/{destinatie}/{data}` — curs incrucisat intre doua valute (via RON)
- `GET /schimb/evolutie/pereche/{sursa}/{destinatie}?start=&end=` — evolutia unui curs incrucisat

**Valute istorice** (ex: BGN, dupa aderarea Bulgariei la zona euro pe 1 ianuarie 2026):
configurate in `routers/schimb.py` (`OBSOLETE_CURRENCIES`), cu `ultima_data_activa` la ultima
zi pentru care BNR a publicat un curs oficial. Toate raspunsurile care includ o astfel de valuta
au campuri suplimentare `istorica`/`sursa_istorica`+`destinatie_istorica` (`true`/`false`) si,
unde e cazul, `ultima_data_activa` — campuri aditionale, structura existenta a raspunsului nu se
schimba. Interogarile pe perioade (`/evolutie`, `/istoric`, `/evolutie/pereche`) care se extind
dupa `ultima_data_activa` sunt limitate automat la aceasta data in loc sa returneze eroare de
date lipsa; interogarile pe o singura zi (`/curs`, `/pereche`) folosesc deja fallback-ul catre
cea mai recenta zi anterioara disponibila.

### Legal holidays (SQLite)

- `GET /zilelibere?start=&end=` — toate zilele libere legale, optional filtrate pe interval
- `GET /zilelibere/luna/{luna}` — zilele libere dintr-o luna (1-12)
- `GET /zilelibere/punti?max_zile_concediu=&min_zile_libere=` — recomandari de "punti"
  (concediu minim pentru un interval liber lung), pe baza sarbatorilor legale si weekendurilor

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
- `GET /companii/{cui}/caen` — CAEN codes for a company by CUI: the principal code plus any
  secondary codes, ordered principal-first then by code. 404 if the company or its CAEN codes are
  not found.
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

4) Optional companies import (inserts new companies only; existing CUIs are skipped):

```powershell
python scripts/import_companies.py --file .\temp\od_firme.csv --truncate
```

To refresh data for companies already in the database (skips CUIs not already present):

```powershell
python scripts/update_companies.py --file .\temp\od_firme.csv
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
