"""Import and enrich every company found in all ONRC identity snapshots.

The ONRC archive under ``temp/onrc`` contains modern ``od_firme.csv`` exports and
older snapshots split into active/inactive and with/without-address files. Formats
changed over time, delimiters vary, and continuation chunks may omit the header.

Files are processed chronologically. A CUI is inserted once; later snapshots enrich
or update it. Empty source values never erase information already collected from a
richer snapshot. The unique constraint on ``companies.cui`` remains the final guard
against duplicates.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select
from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company
from scripts.derive_company_closure_window import snapshot_date
from scripts.import_companies import _row_to_payload
from scripts.update_companies import ADDRESS_FIELDS, GEOCODING_FIELDS


DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "temp" / "onrc"
IDENTITY_COLUMNS = {"DENUMIRE", "CUI", "COD_INMATRICULARE"}
ERROR_LOG_PATH = Path(__file__).resolve().with_name("import_all_companies_errors.log")

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.FileHandler(ERROR_LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


@dataclass
class ImportAllStats:
    files: int = 0
    rows_seen: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    duplicates: int = 0
    errors: int = 0
    geocodes_invalidated: int = 0

    def merge(self, other: "ImportAllStats") -> None:
        for field in self.__dataclass_fields__:
            setattr(self, field, getattr(self, field) + getattr(other, field))


def _looks_like_historical_identity(path: Path) -> bool:
    name = path.name.lower()
    return (
        name.endswith(".csv")
        and name[:1] in "1234"
        and "radiate" in name
        and "sediu" in name
    )


def _has_identity_header(path: Path) -> bool:
    with path.open("r", encoding=_source_encoding(path), errors="replace", newline="") as handle:
        first_line = handle.readline()
    delimiter = "^" if first_line.count("^") >= first_line.count("|") else "|"
    return IDENTITY_COLUMNS.issubset({cell.strip() for cell in first_line.split(delimiter)})


def _source_encoding(path: Path) -> str:
    """Prefer UTF-8, tolerating isolated bad bytes in otherwise UTF-8 exports.

    Some large historical files contain a handful of damaged bytes. Treating the
    entire file as cp1250 after one UTF-8 error turns valid text such as ``Bucureşti``
    into mojibake. Fall back to cp1250 only when replacement characters are common
    enough to indicate that the sample genuinely uses a legacy encoding.
    """
    with path.open("rb") as handle:
        sample = handle.read(256 * 1024)
    try:
        sample.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = sample.decode("utf-8-sig", errors="replace")
        replacement_ratio = decoded.count("\ufffd") / max(len(decoded), 1)
        return "cp1250" if replacement_ratio > 0.002 else "utf-8-sig"
    return "utf-8-sig"


def discover_company_sources(root: Path) -> list[tuple[date, Path]]:
    """Return modern and historical identity files, oldest first."""
    if not root.is_dir():
        raise FileNotFoundError(f"Folderul sursa nu exista: {root}")

    sources: list[tuple[date, Path]] = []
    for path in root.rglob("*.csv"):
        is_modern = path.name.lower() == "od_firme.csv" and _has_identity_header(path)
        if not is_modern and not _looks_like_historical_identity(path):
            continue
        observed_at = snapshot_date(path)
        if observed_at is None:
            print(f"AVERTISMENT: data snapshot-ului nu poate fi dedusa; fisier sarit: {path}")
            continue
        sources.append((observed_at, path))

    return sorted(sources, key=lambda item: (item[0], str(item[1]).lower()))


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE entries from a .env file into the local process environment."""
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ[key] = value


def _infer_header(first_row: list[str]) -> list[str]:
    """Infer the known legacy schema for a headerless continuation chunk."""
    if len(first_row) not in (5, 6):
        raise ValueError(f"format istoric necunoscut ({len(first_row)} coloane)")

    fourth = first_row[3].strip().upper() if len(first_row) > 3 else ""
    has_euid = fourth.startswith("ROONRC")
    if len(first_row) == 5:
        return (
            ["DENUMIRE", "CUI", "COD_INMATRICULARE", "EUID", "STARE_FIRMA"]
            if has_euid
            else ["DENUMIRE", "CUI", "COD_INMATRICULARE", "STARE_FIRMA", "JUDET"]
        )
    return (
        ["DENUMIRE", "CUI", "COD_INMATRICULARE", "EUID", "STARE_FIRMA", "ADRESA"]
        if has_euid
        else ["DENUMIRE", "CUI", "COD_INMATRICULARE", "STARE_FIRMA", "JUDET", "LOCALITATE"]
    )


def iter_source_rows(path: Path) -> Iterator[dict[str, str]]:
    """Yield rows from headed exports and headerless legacy continuation files."""
    with path.open("r", encoding=_source_encoding(path), errors="replace", newline="") as handle:
        first_line = handle.readline()
        if not first_line:
            return
        delimiter = "^" if first_line.count("^") >= first_line.count("|") else "|"
        handle.seek(0)
        reader = csv.reader(handle, delimiter=delimiter)
        first_row = next(reader)
        normalized = [cell.strip() for cell in first_row]

        if IDENTITY_COLUMNS.issubset(set(normalized)):
            header = normalized
        else:
            header = _infer_header(first_row)
            yield dict(zip(header, first_row))

        for row in reader:
            if not row or all(not cell.strip() for cell in row):
                continue
            yield dict(zip(header, row))


def _merge_payload(existing: Company, payload: dict) -> tuple[set[str], bool]:
    """Apply non-empty source values and preserve earliest observed inactive dates."""
    changed: set[str] = set()
    address_changed = False

    for key, value in payload.items():
        if key in {"prima_data_inactiva_cunoscuta", "prima_data_radiata_cunoscuta"}:
            old_value = getattr(existing, key)
            value = min(filter(None, (old_value, value)), default=None)
        elif value is None:
            continue

        if getattr(existing, key) == value:
            continue
        setattr(existing, key, value)
        changed.add(key)
        address_changed = address_changed or key in ADDRESS_FIELDS

    return changed, address_changed


def _upsert_batch(
    payloads: list[dict], *, reset_geocoding: bool
) -> ImportAllStats:
    stats = ImportAllStats(rows_seen=len(payloads))
    deduped = {payload["cui"]: payload for payload in payloads}
    stats.duplicates = len(payloads) - len(deduped)
    batch = list(deduped.values())
    if not batch:
        return stats

    with SessionLocal() as session:
        existing = {
            company.cui: company
            for company in session.scalars(
                select(Company).where(Company.cui.in_(deduped))
            )
        }
        new_rows = [row for row in batch if row["cui"] not in existing]
        stats.inserted = len(new_rows)

        for payload in batch:
            company = existing.get(payload["cui"])
            if company is None:
                continue
            changed, address_changed = _merge_payload(company, payload)
            if not changed:
                stats.unchanged += 1
                continue
            stats.updated += 1
            if address_changed and reset_geocoding:
                if any(getattr(company, field) is not None for field in GEOCODING_FIELDS):
                    stats.geocodes_invalidated += 1
                for field in GEOCODING_FIELDS:
                    setattr(company, field, None)

        if new_rows:
            bind = session.get_bind()
            if bind.dialect.name == "postgresql":
                # Existing deployments may have a historical, non-unique
                # ix_companies_cui index. The preselect above and in-batch dict
                # deduplication already guarantee one insert per absent CUI, so an
                # ON CONFLICT target (which requires a unique constraint) is neither
                # necessary nor portable across those databases.
                session.execute(Company.__table__.insert(), new_rows)
            else:
                session.add_all(Company(**row) for row in new_rows)
        session.commit()
    return stats


def _ensure_existing_cuis_are_unique() -> None:
    """Refuse to enrich an already-ambiguous database.

    Some old deployments have a non-unique index named ``ix_companies_cui``.
    Sequential imports still remain duplicate-free, but pre-existing duplicate CUIs
    would make it arbitrary which company row receives newer information.
    """
    with SessionLocal() as session:
        duplicate_cui = session.scalar(
            select(Company.cui)
            .group_by(Company.cui)
            .having(func.count(Company.id) > 1)
            .limit(1)
        )
    if duplicate_cui is not None:
        raise RuntimeError(
            "Tabela companies contine deja CUI duplicat "
            f"({duplicate_cui}). Consolidati duplicatele inainte de import."
        )


def import_all_companies(
    root: Path = DEFAULT_ROOT,
    *,
    batch_size: int = 5000,
    scan_only: bool = False,
    reset_geocoding: bool = True,
) -> ImportAllStats:
    if batch_size <= 0:
        raise ValueError("batch_size trebuie sa fie mai mare decat zero")

    sources = discover_company_sources(root)
    if not sources:
        raise FileNotFoundError(f"Nu au fost gasite exporturi de firme sub: {root}")

    for source in sources:
        print(f"Fisiere descoperite: {str(source)}")

    if not scan_only:
        init_postgres()
        _ensure_existing_cuis_are_unique()
    total = ImportAllStats()
    print(f"Fisiere identitate descoperite: {len(sources)}")

    for observed_at, path in sources:
        file_stats = ImportAllStats(files=1)
        raw_batch: list[dict] = []
        print(f"[{observed_at}] {path}")
        try:
            for row in iter_source_rows(path):
                file_stats.rows_seen += 1
                payload = _row_to_payload(row, observed_at=observed_at)
                if payload is None:
                    file_stats.errors += 1
                    if scan_only:
                        logger.warning(
                            "Row rejected in %s at row %d: %s",
                            path,
                            file_stats.rows_seen,
                            row,
                        )
                    continue
                if "stare_verificata_la" in payload:
                    payload["stare_verificata_la"] = datetime.combine(
                        observed_at, time.min, tzinfo=timezone.utc
                    )
                raw_batch.append(payload)
                if len(raw_batch) >= batch_size:
                    if scan_only:
                        batch_stats = ImportAllStats(
                            duplicates=len(raw_batch)
                            - len({payload["cui"] for payload in raw_batch})
                        )
                    else:
                        batch_stats = _upsert_batch(
                            raw_batch, reset_geocoding=reset_geocoding
                        )
                    # rows_seen/errors are counted while reading the file.
                    batch_stats.rows_seen = 0
                    file_stats.merge(batch_stats)
                    raw_batch = []
            if raw_batch:
                if scan_only:
                    batch_stats = ImportAllStats(
                        duplicates=len(raw_batch)
                        - len({payload["cui"] for payload in raw_batch})
                    )
                else:
                    batch_stats = _upsert_batch(
                        raw_batch, reset_geocoding=reset_geocoding
                    )
                batch_stats.rows_seen = 0
                file_stats.merge(batch_stats)
        except (OSError, csv.Error, ValueError) as exc:
            print(f"AVERTISMENT: {path} nu a putut fi procesat complet: {exc}")
            file_stats.errors += 1

        total.merge(file_stats)
        print(
            f"  randuri={file_stats.rows_seen}, noi={file_stats.inserted}, "
            f"actualizate={file_stats.updated}, erori={file_stats.errors}"
        )

    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa fara duplicate toate firmele din toate snapshot-urile ONRC"
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Valideaza si numara fisierele/randurile fara conexiune sau scrieri PostgreSQL",
    )
    parser.add_argument(
        "--keep-geocoding",
        action="store_true",
        help="Pastreaza coordonatele chiar daca o adresa mai noua le invalideaza",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".env",
        help="Fisier .env incarcat in mediul local inainte de rulare",
    )
    args = parser.parse_args()

    load_env_file(args.env_file)

    stats = import_all_companies(
        args.root,
        batch_size=args.batch_size,
        scan_only=args.scan_only,
        reset_geocoding=not args.keep_geocoding,
    )
    prefix = "Scanare finalizata" if args.scan_only else "Import finalizat"
    print(f"{prefix}. Fisiere: {stats.files}; randuri: {stats.rows_seen}")
    print(f"Firme noi: {stats.inserted}; actualizate: {stats.updated}; neschimbate: {stats.unchanged}")
    print(f"Duplicate in batch-uri: {stats.duplicates}; erori: {stats.errors}")
    print(f"Geocodari invalidate: {stats.geocodes_invalidated}")


if __name__ == "__main__":
    main()
