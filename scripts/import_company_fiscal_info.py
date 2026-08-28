"""Import MFP taxpayer registry data into company_fiscal_info.

Source: temp/mfp/date_de_identificare_platitori_<snapshot>/date_identificare_platitori_<an>_a.csv
(the "_a" file -- PJ/legal entities; "_b" is PF/PFA and out of scope for `companies`). Contains
things no other imported source has: exact TVA (VAT payer) status, an exact fiscal deregistration
date (DATA_RADIERE -- unlike the approximate window from derive_company_closure_window.py),
telefon/fax, and a separate fiscal address.

Encoding: Windows-1250, confirmed empirically (neither UTF-8 nor cp1252 decode the diacritics
correctly), with occasional bytes undecodable even in cp1250 -- errors="replace" is required, not
optional (a real byte was hit during exploration).

Matched by COD_FISCAL == companies.cui directly -- NOT via registration_number, unlike the rest
of this pipeline (import_company_caen.py, update_company_stare.py). Filtered to
TIP_CONTRIB=="PJ" and TIP_UNITATE=="Sediu central" (excludes filiale/sucursale/puncte de lucru,
which carry their own COD_FISCAL and don't correspond to a `companies` row).

The ~25 IMP*/CONT*/ACCIZE200 DA/NU columns (which taxes/declarations the taxpayer is registered
for) have no legend in any downloaded dataset -- their individual meaning is not guessed at or
split into named columns; they're kept verbatim in `indicatori_fiscali_raw`
("IMP100=DA;IMP120=NU;...").
"""
import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company, CompanyFiscalInfo
from routers.company_utils import clean_text


# Coloane mapate explicit pe campuri proprii -- restul (flag-urile IMP*/CONT*/ACCIZE200) merg in
# indicatori_fiscali_raw. DENUMIRE/COD_FISCAL_PARINTE/SECTOR/JUDET_COMERT/NR_COMERT/AN_COMERT/
# ACT_AUTORIZARE ignorate (fara valoare adaugata fata de ce e deja in `companies`, sau in afara
# scopului acestui import).
_KNOWN_COLUMNS = {
    "COD_FISCAL", "DENUMIRE", "COD_FISCAL_PARINTE", "TIP_UNITATE", "TIP_CONTRIB",
    "LOCALITATE", "STRADA", "NR", "DATA_INREGISTRARE", "DATA_PRELUCRARE", "FAX", "SECTOR",
    "TELEFON", "JUDET_COMERT", "NR_COMERT", "AN_COMERT", "ACT_AUTORIZARE", "TVA",
    "DATA_RADIERE", "COD_POSTAL", "DATA_STARE", "STARE", "JUDET",
    "DETALII_ADRESA", "BLOC", "SCARA", "ETAJ", "AP",
}


def _parse_date(value: str | None) -> date | None:
    """Format sursa: DD.MM.YYYY (punct, nu slash -- company_utils.parse_ro_date nu se
    potriveste, e scris pentru DD/MM/YYYY)."""
    cleaned = clean_text(value)
    if not cleaned:
        return None
    try:
        return datetime.strptime(cleaned, "%d.%m.%Y").date()
    except ValueError:
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    cleaned = clean_text(value)
    if not cleaned:
        return None
    try:
        return datetime.strptime(cleaned, "%d.%m.%Y %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    date_only = _parse_date(cleaned)
    if date_only is None:
        return None
    return datetime.combine(date_only, datetime.min.time(), tzinfo=timezone.utc)


def _build_indicatori_raw(row: dict) -> str | None:
    parts = [
        f"{key}={value.strip()}"
        for key, value in row.items()
        if key is not None and key not in _KNOWN_COLUMNS and value is not None and value.strip()
    ]
    return ";".join(parts) if parts else None


def _row_to_payload(row: dict) -> dict | None:
    cui_raw = clean_text(row.get("COD_FISCAL"))
    if not cui_raw or not cui_raw.isdigit():
        return None

    if clean_text(row.get("TIP_CONTRIB")) != "PJ":
        return None
    if clean_text(row.get("TIP_UNITATE")) != "Sediu central":
        return None

    detalii_parts = [
        clean_text(row.get("DETALII_ADRESA")),
        *(f"{label} {v}" for label, v in (
            ("Bloc", clean_text(row.get("BLOC"))),
            ("Scara", clean_text(row.get("SCARA"))),
            ("Etaj", clean_text(row.get("ETAJ"))),
            ("Ap.", clean_text(row.get("AP"))),
        ) if v),
    ]
    detalii = ", ".join(p for p in detalii_parts if p) or None

    tva_raw = clean_text(row.get("TVA"))
    tva_platitor = None if tva_raw is None else tva_raw == "DA"

    return {
        "cui": int(cui_raw),
        "tva_platitor": tva_platitor,
        "data_inregistrare_fiscala": _parse_date(row.get("DATA_INREGISTRARE")),
        "data_radiere_fiscala": _parse_date(row.get("DATA_RADIERE")),
        "stare_fiscala": clean_text(row.get("STARE")),
        "data_stare_fiscala": _parse_date(row.get("DATA_STARE")),
        "telefon": clean_text(row.get("TELEFON")),
        "fax": clean_text(row.get("FAX")),
        "adresa_fiscala_localitate": clean_text(row.get("LOCALITATE")),
        "adresa_fiscala_judet": clean_text(row.get("JUDET")),
        "adresa_fiscala_strada": clean_text(row.get("STRADA")),
        "adresa_fiscala_numar": clean_text(row.get("NR")),
        "adresa_fiscala_detalii": detalii,
        "cod_postal_fiscal": clean_text(row.get("COD_POSTAL")),
        "indicatori_fiscali_raw": _build_indicatori_raw(row),
        "actualizat_la": _parse_datetime(row.get("DATA_PRELUCRARE")) or datetime.now(timezone.utc),
    }


@dataclass
class ImportStats:
    rows_seen: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_no_company: int = 0
    skipped_filtered_or_invalid: int = 0

    @property
    def processed(self) -> int:
        return self.inserted + self.updated

    def merge(self, other: "ImportStats") -> None:
        self.rows_seen += other.rows_seen
        self.inserted += other.inserted
        self.updated += other.updated
        self.skipped_no_company += other.skipped_no_company
        self.skipped_filtered_or_invalid += other.skipped_filtered_or_invalid


def _read_batches(file_path: Path, batch_size: int):
    batch: list[dict] = []
    skipped = 0

    with file_path.open("r", encoding="cp1250", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="^")
        for row in reader:
            payload = _row_to_payload(row)
            if payload is None:
                skipped += 1
                if len(batch) + skipped >= batch_size:
                    yield batch, skipped
                    batch, skipped = [], 0
                continue

            batch.append(payload)
            if len(batch) >= batch_size:
                yield batch, skipped
                batch, skipped = [], 0

    if batch or skipped:
        yield batch, skipped


def _upsert_batch(batch: list[dict], skipped: int) -> ImportStats:
    stats = ImportStats(rows_seen=len(batch) + skipped, skipped_filtered_or_invalid=skipped)
    if not batch:
        return stats

    with SessionLocal() as session:
        cuis = {row["cui"] for row in batch}
        company_id_by_cui = dict(
            session.execute(select(Company.cui, Company.id).where(Company.cui.in_(cuis))).all()
        )

        payload_by_company_id: dict[int, dict] = {}
        for row in batch:
            company_id = company_id_by_cui.get(row["cui"])
            if company_id is None:
                stats.skipped_no_company += 1
                continue
            payload = {k: v for k, v in row.items() if k != "cui"}
            payload["company_id"] = company_id
            payload_by_company_id[company_id] = payload  # ultima potrivire castiga daca CUI apare de 2 ori
        payload = list(payload_by_company_id.values())

        if not payload:
            return stats

        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            stmt = pg_insert(CompanyFiscalInfo).values(payload)
            update_cols = {k: stmt.excluded[k] for k in payload[0] if k != "company_id"}
            stmt = stmt.on_conflict_do_update(index_elements=[CompanyFiscalInfo.company_id], set_=update_cols)
            session.execute(stmt)
            stats.inserted += len(payload)
        else:
            for row in payload:
                existing = (
                    session.query(CompanyFiscalInfo)
                    .filter(CompanyFiscalInfo.company_id == row["company_id"])
                    .one_or_none()
                )
                if existing is None:
                    session.add(CompanyFiscalInfo(**row))
                    stats.inserted += 1
                else:
                    for key, value in row.items():
                        setattr(existing, key, value)
                    stats.updated += 1
        session.commit()
        print(f"Batch upserted: {len(payload)} rows (inserted={stats.inserted}, updated={stats.updated}, skipped_no_company={stats.skipped_no_company})")
    return stats


def import_company_fiscal_info(file_path: Path, batch_size: int = 5000, truncate: bool = False) -> ImportStats:
    init_postgres()

    if truncate:
        with SessionLocal() as session:
            session.execute(delete(CompanyFiscalInfo))
            session.commit()

    total = ImportStats()
    for batch, skipped in _read_batches(file_path, batch_size=batch_size):
        total.merge(_upsert_batch(batch, skipped))
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import date fiscale MFP (TVA, data radiere, telefon/fax) in company_fiscal_info."
    )
    parser.add_argument("--file", required=True, help="Calea catre fisierul date_identificare_platitori_<an>_a.csv")
    parser.add_argument("--batch-size", type=int, default=5000, help="Numarul de randuri per batch")
    parser.add_argument("--truncate", action="store_true", help="Sterge tabela inainte de import")
    args = parser.parse_args()

    stats = import_company_fiscal_info(Path(args.file), batch_size=args.batch_size, truncate=args.truncate)
    print(f"Import finalizat. Randuri citite: {stats.rows_seen}")
    print(f"Inserate: {stats.inserted}")
    print(f"Actualizate: {stats.updated}")
    print(f"Ignorate (companie negasita dupa CUI): {stats.skipped_no_company}")
    print(f"Ignorate (filtrate TIP_CONTRIB/TIP_UNITATE sau CUI invalid): {stats.skipped_filtered_or_invalid}")


if __name__ == "__main__":
    main()
