# Database setup

This project uses two databases:

- **SQLite** (`caen.db`) — CAEN, SIRUTA, exchange rates, holidays, postal codes. Fully
  scripted, one command, no external downloads needed (source CSVs are checked into
  `temp/`).
- **PostgreSQL** (`companies` DB) — the `/companii` endpoints. Populated from bulk open-data
  files published by ONRC (Trade Registry) and MFP (Ministry of Public Finance) on
  [data.gov.ro](https://data.gov.ro), which you download yourself into `temp/`. There is no
  single "run this one command" step here — it's a handful of independent scripts run in
  order, because the source files come from different datasets, updated on different
  schedules.

---

## 1. SQLite — one command

```bash
python init_db.py
```

Runs, in order (see `init_db.py` for the exact sequence):

| Script | What it does |
|---|---|
| `scripts/init_caen_db.py` | Loads CAEN Rev. 3 codes from `temp/caen_rev3_coduri_clase.csv` (checked in, not downloaded). |
| `scripts/init_siruta_db.py` (`init_siruta()`) | Loads `judete` + `localitati` (all UAT types) from `temp/siruta_cu_diacritice.csv`. |
| `scripts/init_siruta_db.py` (`init_siruta_extins()`) | Additive: `regiuni` (8 NUTS2 regions), `judete.abbr`/`cod_regiune`/`cod_siruta_judet`, and `localitati_componente` (~13.7k sate/component localities) — from `temp/SIRUTA_an_2025/SIRUTA.csv` + `JUDET.DBF` (needs the `dbfread` package), not the simplified CSV above. Matches to `judete`/`localitati` by normalized name, not by numeric code — SIRUTA's own județ numbering doesn't agree with this project's `cod_judet` for every județ (Călărași/Giurgiu are the known exception). Must run after `init_siruta()`. |
| `scripts/init_exchange_db.py` | Downloads 10 years of BNR exchange-rate XML, converts to CSV, imports. |
| `scripts/init_zile_libere_db.py` | Imports Romanian public holidays from a checked-in CSV. |
| `scripts/init_localitati_geo_db.py` | Copies locality name + centroid coordinates (+ builds an R-Tree spatial index, `localitati_geo_rtree`) from a separate PostGIS DB (needs `LOCALITIES_DATABASE_URL`); skipped with a warning if that's not configured. |
| `scripts/match_localitati_geo_siruta.py` | Additive: resolves `localitati_geo.cod_siruta` by matching normalized name+județ against `localitati`/`localitati_componente` (~94% unambiguous match; ambiguous/unmatched rows stay `NULL` rather than guessed). Must run after both `init_siruta_extins()` and `init_localitati_geo_db()`, and again any time the latter reloads from PostGIS (the column doesn't survive a re-fetch). |
| `scripts/init_coduri_postale_db.py` | Imports Posta Romana postal codes; resolves `cod_judet` by name match, so it must run after SIRUTA. |

`scripts/update_exchange_db.py` is the incremental counterpart to `init_exchange_db.py` —
run it later to fetch only new exchange-rate dates instead of re-downloading everything.

---

## 2. PostgreSQL — the company pipeline

Needs `DATABASE_URL` set (Postgres connection string). **Important**: only `main.py` calls
`load_dotenv()` — the scripts below do **not** read `.env` automatically, so export
`DATABASE_URL` in your shell first, or it silently falls back to the docker-internal default
(`db:5432`, unreachable from a host shell):

```powershell
$env:DATABASE_URL="postgresql+psycopg2://user:password@server:port/companies"
```

### 2.0 Download the source files

```bash
python scripts/get_onrc_datasets.py
```

Scrapes the dataset listing pages for the `onrc`, `mfp`, `posta-romana`, and `ancpi`
organizations on data.gov.ro, and downloads every CSV/XLS/XLSX/TXT/ZIP resource it finds into
`temp/<organization>/<dataset-slug>/`. Re-running it skips files that already exist locally.
It gets you *everything* published by those orgs (dozens of datasets, tens of GB); the scripts below only
use a few specific ones out of that pile:

- An ONRC **"firme" dataset** (e.g. `temp/onrc/firme-08-07-2026/`) — periodic full snapshots
  of the trade registry, each folder containing:
  - `od_firme.csv` — company identity + address (name, CUI, registration number, form,
    address fields). No CAEN field.
  - `od_caen_autorizat.csv` — every CAEN activity code authorized per company (see §2.3 —
    **this does not distinguish principal from secondary**).
  - `od_stare_firma.csv` — a status code per registration number (active/suspended/etc.),
    not currently imported anywhere.
- MFP **`situatii_financiare_<year>`** folders (e.g. `temp/mfp/situatii_financiare_2024/`) —
  annual financial statement filings, used in §2.4.

Pick the most recent dataset folder available — these are periodic snapshots, so the newest
one is the least stale. There's no automatic "get the latest" — check `temp/onrc/` yourself.

### 2.1 Import companies

```bash
python scripts/import_companies.py --file temp/onrc/firme-08-07-2026/od_firme.csv --truncate
```

Insert-only: CUIs already in the DB are skipped (`ON CONFLICT DO NOTHING`). `--truncate`
wipes the table first — use it for a from-scratch load, omit it to add on top of what's there.

To refresh companies that already exist (address changes, etc.) from a newer snapshot,
without touching CUIs not yet imported:

```bash
python scripts/update_companies.py --file temp/onrc/firme-08-07-2026/od_firme.csv
```

Everything downstream (§2.2–2.4) matches rows to `companies` by `registration_number` or
`cui`, so this has to run first.

### 2.2 Geocode companies (optional)

```bash
python scripts/geocode_companies.py --batch-size 200 --workers 8
```

Bulk-fills `companies.latitude`/`longitude` via the free, keyless ArcGIS geocoder, so
`GET /companii/{cui}/coordonate` can answer instantly from stored columns instead of
geocoding live on every request. Runs geocode requests in parallel (`--workers`), can resume
(only processes rows with `geocode_status IS NULL` unless `--retry-failed`), and supports
`--limit` (cap rows this run) and `--dry-run` (print what would be geocoded, no API calls, no
writes). Optional rotating proxies via `scripts/proxies.py` (`--no-proxy` to disable).

### 2.3 Import CAEN codes

```bash
python scripts/import_company_caen.py --file temp/onrc/firme-08-07-2026/od_caen_autorizat.csv --truncate
```

Populates `company_caen_codes` (one row per company per authorized activity code). **Read
this carefully — it's the one non-obvious part of this pipeline**:

- The source file lists every authorized code per company, **sorted numerically by CAEN
  code** — not principal-first, not registration-order. ONRC's bulk exports don't mark which
  code is the registered principal activity *anywhere*. An earlier version of this script
  wrongly assumed the first row per company was principal; it wasn't. `is_principal` is now
  always `False` for everything imported from this source — there's currently no reliable way
  to derive it from bulk ONRC data. (ANAF's live TVA-payer lookup, `webservicesp.anaf.ro`,
  does return a single authoritative `cod_CAEN` per company on a live per-CUI call — a
  possible future source for principal, but that's a live lookup, not something you can bulk-
  import for millions of companies.)
- `VER_CAEN_AUTORIZAT` (→ `caen_version`) shows up as either a spelled-out string
  (`"Versiunea 2008"`, older exports) or a bare nomenclature code (`"0"`–`"3"`, newer
  exports); the script normalizes both to the text form. Code `3` = `"Versiunea 2025"` = CAEN
  **Rev 3**, which is what this whole API is supposed to serve — most rows in any snapshot so
  far are still Rev 2 (`"Versiunea 2008"`), since companies convert individually over time,
  not all at once.
- These bulk snapshots lag ONRC's live registry — a company that changed its CAEN code
  recently may still show the old code(s) even in the newest downloaded snapshot. Re-download
  (§2.0) periodically if this matters to you.
- Batches are de-duplicated by `(company_id, caen_code)` before upserting — the same pair can
  appear twice in one batch when a company's rows aren't contiguous in the source file, which
  otherwise crashes Postgres (`ON CONFLICT DO UPDATE command cannot affect row a second time`).

### 2.4 Import financial statements

```bash
python scripts/import_company_financials.py --years 2020-2024
```

Populates `company_financials` (one row per company per fiscal year) from
`temp/mfp/situatii_financiare_<year>/` (default `--root`). Each year's folder has multiple
report-type files (`WEB_BL_BS_SL_AN` = bilant lung, `WEB_UU_AN` = bilant prescurtat, `WEB_IR`
= bilant IFRS); only these three are imported. Column meaning isn't hardcoded — for each file
it reads the matching legend CSV (indicator code → label) and maps labels to canonical fields
via lookup tables in the script, since column order/count differs year to year. Run
`--dry-run` first to see which files it matched for your requested years before importing for
real (some years have duplicate/incomplete folders in the downloaded set). `--truncate-year
<year>` clears one year before re-importing it; omit to upsert on top of what's there.

### 2.5 Refresh precomputed financial statistics

```bash
python scripts/refresh_company_financial_stats.py
```

Populates `company_financial_stats`, which `GET /companii/financiar/statistici` reads for
instant answers to the national / `judet`-only / `caen`-only cases (`sursa: "precalculat"` in
the response). **Not optional if you care about that endpoint's latency**: on this project's
Postgres deployment, disk I/O is slow enough (~4.5 MB/s measured) that a live aggregate over
`company_financials` (13M+ rows) can take several minutes — the query planner prefers a full
sequential scan even filtered to one year, since the `an` index doesn't help enough at that
selectivity to be worth the random I/O on this disk. This script pays that scan cost once,
offline, in a single pass (not once per year/camp, which would take hours) — instant afterward.

Only 3 granularities are precomputed (national, per-`judet`, per-`caen` — not the combined
`judet`+`caen`, and not `localitate`); those combinations, and any `(an, camp)` this hasn't
been run for yet, transparently fall back to the slow live query (`sursa: "live"`). Median is
never precomputed (`mediana` is always `null` for `sursa="precalculat"` rows) — an exact
streaming median needs unbounded per-group memory at this table's scale, judged not worth it
for a first version; the live path still computes it exactly.

**Run this after every `import_company_financials.py` run** (or `import_companies.py`, if it
added/changed which companies have financial data) — the table goes silently stale otherwise,
since the endpoint only re-triggers a live computation when a `(an, camp)` has *no* precomputed
row at all, not when the row it has is outdated.

---

## Recommended order for a from-scratch setup

```bash
python init_db.py                                              # SQLite (§1)

python scripts/get_onrc_datasets.py                             # download ONRC/MFP files (§2.0)
python scripts/import_companies.py --file <od_firme.csv> --truncate
python scripts/import_company_caen.py --file <od_caen_autorizat.csv> --truncate
python scripts/import_company_financials.py --years <e.g. 2015-2024>
python scripts/refresh_company_financial_stats.py               # after every financials import
python scripts/geocode_companies.py                             # optional, slow (external API)
```

## Troubleshooting

- **Everything after step 2.1 is empty / `skipped_no_company` is high**: check `DATABASE_URL`
  is actually exported (see top of §2) — a silent fallback to the docker-internal URL fails
  differently depending on whether anything is listening there.
- **`ForeignKeyViolation` on `company_caen_codes` referencing an unexpected table**: this
  happened once in this project's history (a stale FK pointed at a leftover `companies_bk`
  backup table instead of `companies`). If you restore from an old DB dump, check
  `pg_get_constraintdef` on `company_caen_codes_company_id_fkey` points at `companies(id)`.
- **`/companii/financiar/statistici` is slow even without `localitate` or a `judet`+`caen`
  combo**: `company_financial_stats` hasn't been refreshed for that `(an, camp)` yet — run
  §2.5. Check `sursa` in the response to confirm which path answered.
