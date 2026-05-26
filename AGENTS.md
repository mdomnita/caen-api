# Agent Guidelines for caen-api

## Multi-Agent Environment

This project is actively developed by **multiple AI agents** (Claude Code, Cursor, Antigravity) working
alongside a human developer. Changes from one agent may land in the working tree at any time.

**The single most important rule: do not make changes that will conflict with, undo, or silently
override what another agent or the human developer may be doing concurrently.**

---

## Project Overview

A read-only REST API that exposes the Romanian CAEN Rev. 3 classification codes. Built with:

- **FastAPI** + **slowapi** (rate limiting)
- **SQLite** database (populated by `init_db.py` from `caen_rev3_coduri_clase.csv`)
- **Docker** / `docker-compose` for containerised deployment
- Python virtual environment at `.venv/`

Key files:

| File | Purpose |
|---|---|
| `main.py` | FastAPI application, all endpoints |
| `init_db.py` | One-shot DB initialisation from CSV |
| `requirements.txt` | Python dependencies |
| `Dockerfile` / `docker-compose.yml` | Container config |
| `caen_rev3_coduri_clase.csv` | Source data – **do not modify** |
| `CAEN.sql` | SQL reference – **do not modify** |

---

## Non-Conflict Rules

### 1. Never reformat or restructure files unprompted
- Do **not** run auto-formatters (Black, isort, Ruff `--fix`, etc.) unless the task explicitly
  requires it. Formatting-only commits create noisy diffs that hide real changes and conflict
  with other agents' in-flight work.

### 2. Never rename or move symbols without a clear instruction
- Renaming a function, variable, or module touches every file that imports or calls it.
  Another agent working on the same symbol will produce an immediate merge conflict.

### 3. Never add, remove, or pin dependencies without explicit instruction
- `requirements.txt` is shared. Modifying it without coordination can break another agent's
  currently running environment.

### 4. Scope every change to the minimum required
- Edit only the files directly relevant to the requested task.
- Do not touch `caen_rev3_coduri_clase.csv`, `CAEN.sql`, or `LICENSE` unless the task is
  explicitly about those files.

### 5. Do not delete or overwrite migration / seed data
- `init_db.py` and the CSV are the single source of truth for the database schema and data.
  Never delete, truncate, or silently regenerate them.

### 6. Leave configuration values as-is unless changing them is the task
- `docker-compose.yml`, `Dockerfile`, and environment variables (`DB_PATH`, ports) must not
  be altered as collateral edits.

### 7. Never commit, push, or stage changes autonomously
- Git operations that affect shared history (commit, push, rebase, reset) must be performed
  only when explicitly requested by the human developer.

### 8. Do not modify this file or `CLAUDE.md` without instruction
- These coordination files are written by the human developer and serve as ground truth for
  all agents. Modifying them introduces contradictory guidance.

---

## Safe Operations

The following are always safe to perform without risk of inter-agent conflict:

- Reading any file
- Running the dev server locally (`uvicorn main:app --reload`)
- Running `python init_db.py` to regenerate a local `caen.db`
- Adding new endpoints or Pydantic models *in a clearly delimited block*
- Writing or running tests that do not modify source files

---

## Commands

```bash
# Create virtual environment and install dependencies
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Initialise the SQLite database
python init_db.py

# Run the dev server
uvicorn main:app --reload

# Docker
docker compose up --build
```
