# Romanian CAEN & SIRUTA Codes API

REST API for Romanian CAEN Rev. 3 codes (NACE classification) and SIRUTA locality codes, built with FastAPI and SQLite.

- **CAEN** (Clasificarea Activităților din Economia Națională) — Romanian classification of economic activities, equivalent to the European NACE Rev. 2 standard.
- **SIRUTA** (Sistemul Informatic al Registrului Unităților Teritoriale Administrative) — unique numeric codes for Romanian administrative-territorial units.

Data sources:
- CAEN Rev. 3 full structure (PDF): https://www.onrc.ro/documente/anunturi/CAEN-Rev.3_structura-completa.pdf
- ONRC CAEN index: https://www.onrc.ro/index.php/ro/caen-index
- SIRUTA codes: https://data.gov.ro/dataset/unitati-administrativ-teritoriale-coduri-siruta

---

## Project structure

```
.
├── main.py                                          # FastAPI application entry point
├── auth.py                                          # API key auth, rate limiting, caching helpers
├── init_db.py                                       # Shim — runs scripts/init_caen_db.py
├── routers/
│   ├── caen.py                                      # /caen endpoints
│   ├── ierarhie.py                                  # /sectiuni, /diviziuni, /grupe endpoints
│   └── siruta.py                                    # /siruta endpoints
├── scripts/
│   ├── init_caen_db.py                              # Builds CAEN tables from CSV files
│   └── init_siruta_db.py                            # Builds SIRUTA tables from source data
├── caen_rev3_coduri_clase.csv                       # Source data (651 CAEN classes)
├── caen_rev3_coduri_grupa_diviziune.csv
├── caen_rev3_ierarhic_diviziuni_grupe_clase.csv
├── scrape_to_text.py                                # Scraper used to collect the data
├── CAEN.sql                                         # Legacy PostgreSQL flat table
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

`tip_cod` encodes the hierarchy level (e.g. `12` = municipiu, `23` = oraș, `40` = comună, `70` = sector).

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

#### Example — GET /caen?q=cereale&limit=10&offset=0

```json
{
  "total": 2,
  "results": [...]
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

#### Example — GET /siruta/judete

```json
[
  { "cod_judet": 1, "denumire": "ALBA" },
  { "cod_judet": 2, "denumire": "ARAD" },
  ...
]
```

---

## Running locally

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt

python init_db.py             # creates CAEN tables in caen.db
python scripts/init_siruta_db.py  # adds SIRUTA tables to caen.db

uvicorn main:app --reload
```

Open http://localhost:8000/docs for the interactive Swagger UI, or http://localhost:8000/redoc for ReDoc.

## Docker

```bash
docker compose up -d --build
```

The API will be available at http://localhost:8000.

The SQLite database is built inside the container at image build time — no external database service required.
