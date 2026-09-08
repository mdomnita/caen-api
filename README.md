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
│   ├── companies.py            # /companii endpoints (search, filter, caen, bilant, financiar*, comparatie, coordonate)
│   ├── company_database.py     # PostgreSQL engine, session factory, init_postgres()
│   ├── company_models.py       # SQLAlchemy models: Company, CompanyCaenCode, CompanyFinancial, CompanyFinancialStats
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
│   ├── get_onrc_datasets.py    # downloads ONRC/MFP/Posta Romana/ANCPI open-data files into temp/
│   ├── import_companies.py     # inserts only new companies (by CUI), skips existing ones
│   ├── update_companies.py     # refreshes existing companies by CUI; dry-run + stale-geocode invalidation
│   ├── import_company_caen.py  # authorized CAEN codes (company_caen_codes) from ONRC od_caen_autorizat.csv
│   ├── import_company_financials.py    # financial statements (company_financials) from MFP situatii_financiare
│   ├── refresh_company_financial_stats.py  # precomputed aggregates for GET /companii/financiar/statistici
│   ├── geocode_companies.py    # bulk lat/lon via ArcGIS for GET /companii/{cui}/coordonate
│   ├── update_company_stare.py           # Stare curenta + date observate din snapshot-uri ONRC
│   ├── update_company_caen_principal.py  # CompanyCaenCode.is_principal via live ANAF PlatitorTva v9
│   ├── derive_company_closure_window.py  # approximate closure window from historical ONRC snapshots
│   ├── import_company_fiscal_info.py     # TVA/exact radiere/telefon/fax from MFP taxpayer registry
│   └── import_company_representatives.py # administrators/lichidatori from ONRC od_reprezentanti_legali.csv
├── Dockerfile
└── docker-compose.yml
```

See [DATABASE_SETUP.md](DATABASE_SETUP.md) for what each company-pipeline script does and the order to run them in.

## Authentication and rate limiting

All endpoints accept optional `X-API-KEY`.

| Scenario | Rate limit |
|---|---|
| No key | 10 requests / minute per IP |
| Valid `X-API-KEY` | 100 requests / minute |
| Invalid `X-API-KEY` | 403 Forbidden |

This project runs independently and can be supported via GitHub Sponsors.

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

- `GET /siruta/judete` — toate judetele (cod + denumire)
- `GET /siruta/judete/{cod_judet}` — detalii judet (abreviere auto, regiune de dezvoltare, cod
  SIRUTA propriu al judetului)
- `GET /siruta/judete/abbr/{abbr}` — detalii judet dupa abrevierea auto (ex: CJ, MS, B)
- `GET /siruta/regiuni` — toate regiunile de dezvoltare (NUTS2)
- `GET /siruta/regiuni/{cod_regiune}/judete` — judetele dintr-o regiune de dezvoltare
- `GET /siruta/judet/{cod_judet}?tip_cod=` — toate localitatile (UAT-uri) dintr-un judet; `tip_cod`
  optional filtreaza dupa tip de UAT (12=municipiu, 13=oras, 14=comuna, 16=sector)
- `GET /siruta/localitate/{cod}` — lookup dupa cod SIRUTA; cauta intai printre UAT-uri, apoi (daca
  nu gaseste) printre sate/localitati componente — raspunsul include `nivel` ("UAT" sau
  "componenta") si, pentru sate, `cod_siruta_parinte`
- `GET /siruta/localitate/{cod}/componente` — localitatile componente (sate apartinatoare etc.) ale
  unui UAT parinte, cu lat/lon (potrivire cu `localitati_geo`) si coduri postale
- `GET /siruta/cautare?q={text}&limit=&offset=` — cauta dupa nume atat UAT-uri (municipii, orase,
  comune, sectoare) cat si sate/localitati componente, intr-un singur rezultat unificat; fiecare
  rezultat are `nivel` ("UAT" sau "componenta")
- `GET /siruta/tipuri` — nomenclator complet al tipurilor de localitati (UAT si componenta)

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
- `GET /companii?judet=&localitate=&caen=&forma_juridica=&are_coordonate=&an=&cifra_afaceri_min=&...&sort=&limit=&offset=` —
  advanced filtering/listing for market research: county/locality, CAEN (principal or secondary,
  via `company_caen_codes`), legal form, presence of geocoded coordinates, and financial
  thresholds (turnover, profit, employees, debts, current assets) for a given fiscal year.
  `sort` takes a field name optionally prefixed with `-` for descending (e.g. `-cifra_afaceri`).
  Financial filtering/sorting requires `an`. `total` here is the full match count (supports
  `limit`/`offset` pagination), unlike `/search`'s `total`.
- `GET /companii/{cui}` — full company record by CUI, including address, legal form, registration
  details, and legal representatives. Each representative exposes only `name` and `role`; birth
  and residence data from the source table are intentionally omitted. The full response now
  includes the same nested collection:

  ```json
  {
    "name": "TRANSIDEAL SRL",
    "cui": 412052,
    "registration_number": "J40/…",
    "representatives": [
      {"name": "MILITARU NICOLAE", "role": "administrator"},
      {"name": "POPESCU PETRE", "role": "administrator"}
    ]
  }
  ```

  The other company fields are unchanged and are omitted from this shortened example.
- `GET /companii/{cui}/representatives` — legal representatives imported from ONRC for the
  company. Returns `200` with an empty list when the company exists without representative data,
  and `404` when the CUI is unknown. Example:

  ```json
  {
    "cui": 412052,
    "representatives": [
      {"name": "MILITARU NICOLAE", "role": "administrator"},
      {"name": "POPESCU PETRE", "role": "administrator"}
    ]
  }
  ```
- `GET /companii/{cui}/caen` — CAEN codes for a company by CUI (principal + secondary from
  `company_caen_codes`, ordered principal-first then by code). **`principal` is currently always
  `null`**: ONRC's bulk open-data export doesn't mark which authorized code is the registered
  principal activity anywhere (rows just sort numerically by code), so there's no reliable source
  for it yet — see `DATABASE_SETUP.md` §2.3. 404 if the company or its CAEN codes are not found.
- `GET /companii/{cui}/bilant?ani=2022&ani=2023` — financial statements (bilant) fetched **live
  from ANAF** for one or more fiscal years, in parallel via the ANAF public webservice. Default:
  last fiscal year (`current_year - 1`). Maximum 5 years per request. Response includes `name`,
  `caen_code`, `caen_label`, and a `years` list each containing 20 standardised financial
  indicators (I1–I20). A `warning` field is populated when more than one year is requested.
  Distinct from the `/financiar` family below, which reads from the local `company_financials`
  table instead of calling ANAF.
- `GET /companii/{cui}/bilant/ultimul-an` — walks backward year by year from `current_year - 1`
  down to 2014, returning the first fiscal year for which ANAF has bilant data. Useful for
  closed/deregistered companies whose most recent years have no filed statements (e.g. a company
  deregistered in 2013 returns the 2012 bilant). Same response shape as `/bilant` with a single
  `years` entry. 404 if no bilant is found down to 2014.
- `GET /companii/{cui}/financiar?ani=&an_start=&an_end=&campuri=` — financial data for a company
  read from the local `company_financials` table (imported by
  `scripts/import_company_financials.py`), not ANAF. Filter by explicit `ani` (repeatable) or an
  `an_start`/`an_end` range (`ani` wins if both given); default is the company's latest available
  year. `campuri` (repeatable) restricts which of the 17 stored fields are returned per year;
  default is all of them.
- `GET /companii/{cui}/financiar/evolutie?camp=&ani=&an_start=&an_end=` — single-indicator time
  series (one field, all/selected years, ascending) for charting.
- `GET /companii/{cui}/financiar/indicatori?ani=&an_start=&an_end=` — ratios derived on read from
  stored fields: profit margin, revenue per employee, and year-over-year growth (relative to the
  nearest earlier year actually present in the response, not necessarily `an - 1`).
- `GET /companii/financiar/clasament?an=&camp=&caen=&county=&limit=` — cross-company leaderboard:
  top firms by one indicator for one fiscal year, optionally filtered by CAEN or county. Firms
  without a value for that indicator/year are excluded. An empty result is `200`, not `404`
  (search-style endpoint, like `/search`).
- `GET /companii/financiar/statistici?an=&camp=&judet=&localitate=&caen=` — aggregate stats
  (count, sum, average, median, min, max) for a filtered group of companies. National /
  `judet`-only / `caen`-only requests answer instantly from a precomputed table
  (`sursa: "precalculat"`, refreshed by `scripts/refresh_company_financial_stats.py`; `mediana`
  is always `null` for these); `localitate`, or `judet`+`caen` together, compute live
  (`sursa: "live"`, exact median included) — see `DATABASE_SETUP.md` §2.5 for why this distinction
  exists.
- `GET /companii/comparatie?cui=&cui=&...&an=` — compares 2–20 named companies (turnover, profit,
  employees, assets, debts, margin, revenue/employee, YoY growth) side by side. Without `an`, each
  company uses its own latest available year (may differ between companies). Unknown CUIs are
  reported in `cui_negasite` rather than failing the request.
- `GET /companii/{cui}/coordonate` — latitude/longitude for a company. Returns instantly from
  stored `latitude`/`longitude` columns when populated by the bulk
  `scripts/geocode_companies.py` run (`sursa=stocat`); otherwise geocodes the company's address
  live via ArcGIS (`sursa=live`). Live geocoding results are never written back to the database.

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
$env:DATABASE_URL="postgresql+psycopg2://user:password@host:port/companies"
```

4) Optional: populate the companies dataset (identity, CAEN codes, financial statements,
   geocoding, precomputed stats). This is a multi-step pipeline sourced from ONRC/MFP open data,
   not a single command — see **[DATABASE_SETUP.md](DATABASE_SETUP.md)** for the full script list,
   what each one does, and the order to run them in.

5) Start API:

```bash
uvicorn main:app --reload
```

Swagger UI: http://localhost:8000/docs

## Docker

Start the API:

```bash
docker compose up -d --build
```

Compose topology (`docker-compose.yml`): a single `api` service (FastAPI, `main:app`),
bind-mounting `./data` for the SQLite database. **PostgreSQL is not part of this compose
file** — point `DATABASE_URL` (via `.env`, loaded through `env_file`) at a Postgres instance you
run/manage separately. There is no bundled importer service or `tools` profile; run the company
pipeline scripts from `DATABASE_SETUP.md` against that Postgres instance directly (from the host,
or `docker compose exec api ...` if you'd rather run them inside the container).
