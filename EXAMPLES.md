# API query examples

Example requests for every endpoint, grouped the same way as the endpoint list in
[README.md](README.md#api-endpoints). Assumes a local direct run (`uvicorn main:app --reload`,
no `/api` prefix — see [Root path behavior](README.md#root-path-behavior-api) in the README).
Swap `http://localhost:8000` for your deployment's base URL, adding `/api` if it's mounted
behind a reverse proxy.

All requests accept an optional `X-API-KEY` header for the higher rate limit (see README).

---

## CAEN and hierarchy

```bash
# Lookup by exact CAEN code (2-4 digits)
curl "http://localhost:8000/caen/0111"

# Full-text search by code or name
curl "http://localhost:8000/caen?q=cereale"
curl "http://localhost:8000/caen?q=0111"

# CAEN Rev.2 <-> Rev.3 correspondence search (needs v2 and/or v3)
curl "http://localhost:8000/caen/corespondenta?v3=0111"
curl "http://localhost:8000/caen/corespondenta?v2=0113&tip=AGREGARE"

# CAEN Rev.2 detail + its Rev.3 correspondences
curl "http://localhost:8000/caen/v2/0111"

# CAEN Rev.2 codes a Rev.3 code originated from
curl "http://localhost:8000/caen/v3/0111/v2"

# Hierarchy navigation: Sectiuni -> Diviziuni -> Grupe -> Clase
curl "http://localhost:8000/sectiuni"
curl "http://localhost:8000/sectiuni/A"
curl "http://localhost:8000/sectiuni/A/diviziuni"
curl "http://localhost:8000/diviziuni/01"
curl "http://localhost:8000/diviziuni/01/grupe"
curl "http://localhost:8000/grupe/011"
curl "http://localhost:8000/grupe/011/clase"
```

## SIRUTA

```bash
# All counties
curl "http://localhost:8000/siruta/judete"

# Lookup by SIRUTA code (174744 = Focsani)
curl "http://localhost:8000/siruta/localitate/174744"

# Search by name (diacritics-insensitive)
curl "http://localhost:8000/siruta/cautare?q=Focsani"
curl --get "http://localhost:8000/siruta/cautare" --data-urlencode "q=Focșani" --data-urlencode "limit=10"

# All localities in a county (41 = Vrancea); optional tip_cod filter
# (12=municipiu, 13=oras, 14=comuna, 16=sector)
curl "http://localhost:8000/siruta/judet/41"
curl "http://localhost:8000/siruta/judet/41?tip_cod=12"
```

## Localitati geo

Requires `localitati_geo` to be populated (`scripts/init_localitati_geo_db.py`, needs
`LOCALITIES_DATABASE_URL`) — names + lat/lon centroids, no SIRUTA codes (see `/siruta` above
for those).

```bash
# Search by name
curl --get "http://localhost:8000/localitati/search" --data-urlencode "q=Focșani"

# Lookup by exact name; judet disambiguates homonyms across counties
curl --get "http://localhost:8000/localitati/localitate/Focșani" --data-urlencode "judet=Vrancea"

# All localities in a county, by name
curl --get "http://localhost:8000/localitati/judet/Vrancea"
```

## Postal codes

Source: [data.gov.ro coduri-postale-romania](https://data.gov.ro/dataset/coduri-postale-romania).

```bash
# Lookup by 6-digit postal code (returns a list — codes aren't unique)
curl "http://localhost:8000/coduripostale/011357"

# Combined filter search (at least one of judet/localitate/strada/numar is required)
curl --get "http://localhost:8000/coduripostale/cautare" \
  --data-urlencode "judet=Vrancea" --data-urlencode "localitate=Focsani"

curl --get "http://localhost:8000/coduripostale/cautare" \
  --data-urlencode "strada=Cuza Voda" --data-urlencode "numar=10" --data-urlencode "limit=20"

# numar can match both a street-number range (nr.) and an unrelated block (bl.)
# sharing the same digit -- use numar_tip to disambiguate
curl --get "http://localhost:8000/coduripostale/cautare" \
  --data-urlencode "strada=Donath" --data-urlencode "numar=38" --data-urlencode "numar_tip=nr"

# Autocomplete (type-ahead); tip=strada requires localitate to scope results
curl --get "http://localhost:8000/coduripostale/autocomplete" \
  --data-urlencode "tip=judet" --data-urlencode "q=Vra"

curl --get "http://localhost:8000/coduripostale/autocomplete" \
  --data-urlencode "tip=strada" --data-urlencode "q=Cuz" --data-urlencode "localitate=Focsani"

# Free-form address resolution: local match first, ArcGIS fallback if nothing local matches
curl --get "http://localhost:8000/coduripostale/rezolvare" \
  --data-urlencode "adresa=Str. Cuza Voda, Focsani, Vrancea"

curl --get "http://localhost:8000/coduripostale/rezolvare" \
  --data-urlencode "adresa=10 Downing Street, London, United Kingdom"
```

## Exchange rates

```bash
# Latest rate for every currency, vs RON
curl "http://localhost:8000/schimb/valute"

# All rates on a given date (or nearest prior trading day)
curl "http://localhost:8000/schimb/valute/2026-07-29"

# A single currency's rate on a date
curl "http://localhost:8000/schimb/curs/EUR/2026-07-29"

# Evolution over a period (end defaults to today)
curl --get "http://localhost:8000/schimb/evolutie/EUR" --data-urlencode "start=2026-07-01"

# Same, but with a mandatory interval and no wrapper object
curl --get "http://localhost:8000/schimb/istoric/EUR" \
  --data-urlencode "from=2026-07-01" --data-urlencode "to=2026-07-29"

# Cross rate between two currencies (via RON); RON is a valid source/destination too
curl "http://localhost:8000/schimb/pereche/EUR/USD/2026-07-29"

# Cross-rate evolution over a period
curl --get "http://localhost:8000/schimb/evolutie/pereche/EUR/USD" --data-urlencode "start=2026-07-01"

# Historical currency (BGN, obsolete since Bulgaria adopted the euro on 2026-01-01):
# responses include istorica/ultima_data_activa; a range extending past the
# last published date is clamped instead of returning a missing-data error.
curl "http://localhost:8000/schimb/curs/BGN/2026-07-29"
curl --get "http://localhost:8000/schimb/evolutie/BGN" --data-urlencode "start=2025-06-01" --data-urlencode "end=2026-07-29"
```

## Legal holidays

```bash
# All legal holidays, optionally filtered by interval
curl "http://localhost:8000/zilelibere"
curl --get "http://localhost:8000/zilelibere" --data-urlencode "start=2026-01-01" --data-urlencode "end=2026-03-31"

# Holidays in a given month (1-12)
curl "http://localhost:8000/zilelibere/luna/1"

# "Punte" (bridge day-off) recommendations
curl "http://localhost:8000/zilelibere/punti"
curl --get "http://localhost:8000/zilelibere/punti" --data-urlencode "max_zile_concediu=1" --data-urlencode "min_zile_libere=4"
```

## Companies (PostgreSQL)

Requires the companies dataset to be imported (`scripts/import_companies.py`). Replace
`12345678` with a real CUI from your database.

```bash
# Fuzzy name search (prefix + trigram similarity)
curl --get "http://localhost:8000/companii/search" --data-urlencode "q=dacia" --data-urlencode "limit=10"

# Autocomplete (prefix-only, fastest for type-ahead)
curl --get "http://localhost:8000/companii/autocomplete" --data-urlencode "q=dacia"

# Full record by CUI
curl "http://localhost:8000/companii/12345678"

# CAEN codes (principal + secondary) for a company
curl "http://localhost:8000/companii/12345678/caen"

# Financial statements (ANAF), one or more fiscal years (default: last fiscal year, max 5)
curl "http://localhost:8000/companii/12345678/bilant"
curl --get "http://localhost:8000/companii/12345678/bilant" --data-urlencode "ani=2022" --data-urlencode "ani=2023"

# Last fiscal year with an available bilant (walks backward from last fiscal year to 2014;
# useful for closed/deregistered companies)
curl "http://localhost:8000/companii/12345678/bilant/ultimul-an"

# Coordinates (lat/lon): instant if stored in DB (sursa=stocat), else live ArcGIS geocoding (sursa=live)
curl "http://localhost:8000/companii/12345678/coordonate"
```
