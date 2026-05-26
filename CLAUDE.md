# CLAUDE.md — caen-api

Instructions for Claude Code when working in this repository.

---

## Project

Read-only REST API serving Romanian CAEN Rev. 3 classification codes.

- **FastAPI** application in `main.py`
- **SQLite** database initialised by `init_db.py` from `caen_rev3_coduri_clase.csv`
- Rate-limited with **slowapi** (10 req/min per IP)
- Containerised with `Dockerfile` + `docker-compose.yml`
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

- Keep endpoints in `main.py`; do not split into multiple modules unless instructed.
- All database access goes through the `get_db()` context manager.
- Rate-limit every new endpoint with `@limiter.limit("10/minute")`.
- Return `HTTPException(404)` for missing resources; never return empty 200 responses.
- Do not add logging, metrics, or middleware unless explicitly requested.
- The API is read-only (GET only); do not add write endpoints.
