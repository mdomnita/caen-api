# Romanian CAEN, SIRUTA & Exchange Rates API

REST API for Romanian CAEN Rev. 3 codes, SIRUTA locality codes, and BNR daily exchange rates — built with FastAPI and SQLite.

- **CAEN** (Clasificarea Activităților din Economia Națională) — Romanian classification of economic activities, equivalent to the European NACE Rev. 2 standard.
- **SIRUTA** (Sistemul Informatic al Registrului Unităților Teritoriale Administrative) — unique numeric codes for Romanian administrative-territorial units.
- **Schimb valutar** — daily reference exchange rates published by the National Bank of Romania (BNR), covering 39 currencies from 2005 to present.

Data sources:
- CAEN Rev. 3 full structure (PDF): https://www.onrc.ro/documente/anunturi/CAEN-Rev.3_structura-completa.pdf
- ONRC CAEN index: https://www.onrc.ro/index.php/ro/caen-index
- SIRUTA codes: https://data.gov.ro/dataset/unitati-administrativ-teritoriale-coduri-siruta
- BNR exchange rates: https://www.bnr.ro/files/xml/years/nbrfxrates{year}.xml

---

## Project structure

```
.
├── main.py                         # FastAPI application entry point
├── auth.py                         # API key auth, rate limiting, caching helpers
├── init_db.py                      # Initialises all tables (CAEN + SIRUTA + exchange rates)
├── routers/
│   ├── caen.py                     # /caen endpoints
│   ├── ierarhie.py                 # /sectiuni, /diviziuni, /grupe endpoints
│   ├── siruta.py                   # /siruta endpoints
│   └── schimb.py                   # /schimb endpoints
├── scripts/
│   ├── init_caen_db.py             # Builds CAEN tables from CSV
│   ├── init_siruta_db.py           # Builds SIRUTA tables from CSV
│   └── init_exchange_db.py         # Downloads BNR XML, converts to CSV, builds exchange table
├── temp/
│   ├── exchange_rates/
│   │   ├── xml/                    # Original BNR XML files (cached locally)
│   │   └── csv/                    # Converted CSV files (one per year)
│   └── ...                         # SIRUTA source files
├── caen_rev3_coduri_clase.csv      # Source data (651 CAEN classes)
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

## Database schema

### CAEN tables

```
sectiuni  (A, B, C…)
  └── diviziuni  (01, 02…)
        └── grupe  (011, 012…)
              └── clase  (0111, 0112…)  ← CAEN 4-digit codes
```

### SIRUTA tables

```
judete  (cod_judet, denumire)
  └── localitati  (cod_siruta, denumire, tip_cod, tip_abrev, tip_denumire, cod_judet)
```

`tip_cod` encodes the hierarchy level (`12` = municipiu, `13` = oraș, `14` = comună, `16` = sector).

### Exchange rates table

```
cursuri_valutare (data, valuta, curs, multiplicator)
```

`curs` is the raw BNR value. For currencies where `multiplicator = 100` (HUF, JPY, IDR, ISK, KRW), the rate applies per 100 units. All API responses expose a normalised `curs_unitar = curs / multiplicator` field representing the value of 1 unit in RON.

---

## Authentication & rate limiting

All endpoints accept an optional `X-API-KEY` header.

| Scenario | Rate limit |
|---|---|
| No key (anonymous) | 10 requests / minute per IP |
| Valid `X-API-KEY` | 1 000 requests / minute |
| Invalid `X-API-KEY` | `403 Forbidden` |

Responses include `Cache-Control: public, max-age=86400` and `ETag` headers. Clients that send `If-None-Match` will receive `304 Not Modified` when content has not changed.

---

## API endpoints

### CAEN — codes and hierarchy

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/caen/{cod}` | Exact lookup by 2–4 digit CAEN code |
| `GET` | `/caen?q={text}` | Full-text search by code or name |
| `GET` | `/sectiuni` | List all sections (A, B, C…) |
| `GET` | `/sectiuni/{cod}` | Section detail |
| `GET` | `/sectiuni/{cod}/diviziuni` | Divisions within a section |
| `GET` | `/diviziuni/{cod}` | Division detail |
| `GET` | `/diviziuni/{cod}/grupe` | Groups within a division |
| `GET` | `/grupe/{cod}` | Group detail |
| `GET` | `/grupe/{cod}/clase` | All CAEN classes within a group |

Search accepts optional `limit` (1–200, default 50) and `offset` (default 0) query parameters.

#### Example — GET /caen/0111

```json
{
  "cod_caen": "0111",
  "denumire": "Cultivarea cerealelor (excluzând orezul), plantelor leguminoase şi a plantelor oleaginoase",
  "sectiune_cod": "A",
  "sectiune": "AGRICULTURĂ, SILVICULTURĂ ŞI PESCUIT",
  "diviziune_cod": "01",
  "diviziune": "Agricultură, vânătoare şi servicii anexe",
  "grupa_cod": "011",
  "grupa": "Cultivarea plantelor nepermanente"
}
```

---

### SIRUTA — administrative-territorial units

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/siruta/judete` | List all counties |
| `GET` | `/siruta/localitate/{cod}` | Locality lookup by SIRUTA code |
| `GET` | `/siruta/cautare?q={text}` | Search localities by name |
| `GET` | `/siruta/judet/{cod_judet}` | All localities in a county |

`/siruta/cautare` accepts `limit` (1–200, default 50) and `offset` (default 0).

`/siruta/judet/{cod_judet}` accepts an optional `tip_cod` query parameter to filter by hierarchy level.

#### Example — GET /siruta/localitate/666

```json
{
  "cod_siruta": 666,
  "denumire": "FOCŞANI",
  "tip_cod": 12,
  "tip_abrev": "Mun.",
  "tip_denumire": "Municipiu",
  "cod_judet": 41,
  "judet_denumire": "VRANCEA"
}
```

---

### Schimb valutar — BNR exchange rates

BNR publishes rates on working days only. Endpoints that accept a date fall back to the most recent prior trading day when the requested date falls on a weekend or holiday.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/schimb/valute` | All 39 available currencies with their latest rate vs RON |
| `GET` | `/schimb/curs/{valuta}/{data}` | Rate of `{valuta}` vs RON on `{data}` (YYYY-MM-DD) |
| `GET` | `/schimb/evolutie/{valuta}?start=&end=` | Time series of `{valuta}` vs RON for a date range |
| `GET` | `/schimb/pereche/{sursa}/{destinatie}/{data}` | Cross-rate between any two currencies on `{data}` (via RON) |
| `GET` | `/schimb/evolutie/pereche/{sursa}/{destinatie}?start=&end=` | Cross-rate time series for a date range |

Use `RON` as `sursa` or `destinatie` to get the inverse RON rate directly. `end` defaults to today when omitted.

#### Example — GET /schimb/curs/EUR/2025-06-01

```json
{
  "data": "2025-05-30",
  "valuta": "EUR",
  "curs": 5.0802,
  "multiplicator": 1,
  "curs_unitar": 5.0802
}
```

*(Date falls back to Friday 30 May because 1 June 2025 was a Sunday.)*

#### Example — GET /schimb/pereche/EUR/USD/2025-01-15

```json
{
  "data": "2025-01-15",
  "sursa": "EUR",
  "destinatie": "USD",
  "curs": 1.029485
}
```

#### Example — GET /schimb/evolutie/EUR?start=2025-01-01&end=2025-01-31

```json
{
  "sursa": "EUR",
  "destinatie": "RON",
  "date_start": "2025-01-01",
  "date_end": "2025-01-31",
  "puncte": [
    { "data": "2025-01-02", "curs": 4.9748 },
    { "data": "2025-01-03", "curs": 4.9748 },
    ...
  ]
}
```

---

## Running locally

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt

python init_db.py             # CAEN + SIRUTA + exchange rates (all in one)

uvicorn main:app --reload
```

Open http://localhost:8000/docs for the interactive Swagger UI, or http://localhost:8000/redoc for ReDoc.

The first run of `init_db.py` downloads 10 years of BNR XML files (~10 MB) into `temp/exchange_rates/xml/` and caches them locally. Subsequent runs skip the download.

Individual scripts can also be run independently:

```bash
python scripts/init_caen_db.py       # CAEN only
python scripts/init_siruta_db.py     # SIRUTA only
python scripts/init_exchange_db.py   # Exchange rates — full re-import from 2005
python scripts/update_exchange_db.py # Exchange rates — incremental update (new dates only)
```

`update_exchange_db.py` checks the latest date already in the database and imports only newer records. The current year's XML cache is always refreshed so today's rates are fetched from BNR. Run it daily (e.g. via cron or Task Scheduler) to keep exchange rates current.

## Docker

```bash
docker compose up -d --build
```

The API will be available at http://localhost:8000.

The SQLite database is built inside the container at image build time — no external database service required.
