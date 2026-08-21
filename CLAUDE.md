# CLAUDE.md — caen-api

Instructions for Claude Code when working in this repository.

---

## Project

Read-only REST API serving Romanian CAEN Rev. 3 codes, SIRUTA locality codes, postal codes, BNR
daily exchange rates, public holidays, and a PostgreSQL-backed company dataset (search, CAEN
codes, financial data, geocoding) under `/companii`.

- **FastAPI** application in `main.py`; routers in `routers/` (`caen`, `ierarhie`, `siruta`,
  `localitati`, `coduripostale`, `schimb`, `zilelibere`, `companies`) — see `README.md` for the
  full endpoint list per router.
- **SQLite** database initialised by `init_db.py` (runs CAEN + SIRUTA + exchange rate + holidays +
  postal code scripts in sequence). **PostgreSQL** database for `/companii`, populated by a
  separate set of scripts — see `DATABASE_SETUP.md`, not `init_db.py`.
- Rate-limited with **slowapi** (10 req/min per IP)
- Containerised with `Dockerfile` + `docker-compose.yml` (API only — PostgreSQL runs externally)
- Python virtual environment: `.venv/`

---

## Multi-Agent Environment

This repo is worked on concurrently by Claude Code, Cursor, Antigravity, and the human developer.
Read [`AGENTS.md`](AGENTS.md) before making any changes — it is the authoritative coordination
document for all agents.

**Summary of critical constraints:**

- Do **not** reformat or restructure files outside the scope of the task.
- Do **not** rename symbols, move files, or refactor without explicit instruction.
- Do **not** modify `requirements.txt`, `docker-compose.yml`, or `Dockerfile` as collateral changes.
- Do **not** touch `caen_rev3_coduri_clase.csv`, `CAEN.sql`, or `LICENSE` unless the task is
  specifically about those files.
- Do **not** run `git commit`, `git push`, or any destructive git command without explicit approval.
- Do **not** modify `AGENTS.md` or this file without instruction.

---

## Commands

```bash
# Activate venv (Windows)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Initialise / reset the database
python init_db.py

# Run dev server
uvicorn main:app --reload

# Docker
docker compose up --build
```

---

## Code Conventions

- Keep endpoints in their respective router files under `routers/`; add new domains as new router files.
- SQLite access goes through the `get_db()` context manager (`auth.py`); PostgreSQL (`/companii`)
  access goes through `get_company_session` (`api_dependencies.py` / `routers/company_database.py`).
  Section-to-database routing is dispatched in `api_dependencies.py`.
- Rate-limit every new endpoint with `@limiter.limit(_dynamic_limit)` (see existing routers —
  `_dynamic_limit` is what's actually used, not a hardcoded `"10/minute"` string).
- Return `HTTPException(404)` for single-resource fetches with no match (e.g. `/companii/{cui}`).
  Search/filter/aggregate endpoints (`/search`, `/companii` filtering, `/financiar/clasament`,
  `/financiar/statistici`) return `200` with an empty/zero result instead — an empty result set is
  a valid answer for those, not a missing resource. Never return an empty `200` for a
  single-resource lookup that should have been a `404`.
- Do not add logging, metrics, or middleware unless explicitly requested.
- The API is read-only (GET only); do not add write endpoints.
