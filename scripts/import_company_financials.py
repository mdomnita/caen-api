"""Import indicatori financiari anuali (MFP - situatii financiare) in PostgreSQL.

Sursa: seturile de date "situatii financiare" de pe date.gov.ro, descarcate sub
temp/mfp/situatii_financiare_*. Fiecare an contine mai multe tipuri de raportari;
scriptul importa doar cele 3 relevante pentru firme comerciale "obisnuite"
(vezi descriere-fisiere-web-bilant.txt):

    WEB_BL_BS_SL_AN - bilant lung
    WEB_UU_AN       - bilant format prescurtat
    WEB_IR          - bilant IFRS

Denumirile fisierelor si numarul/ordinea indicatorilor difera de la an la an, asa
ca maparea coloanelor NU e hardcodata: pentru fiecare fisier de date se citeste
fisierul-legenda (.csv) asociat, care leaga codul de coloana (i1, i2, ...) de
eticheta indicatorului, iar eticheta e mapata la un camp canonic prin
CANONICAL_FIELDS / SIGNED_FIELDS.

Ruleaza `--dry-run` intai pentru a verifica maparea an -> fisiere descoperita,
dat fiind ca sursa contine foldere duplicate/incomplete pe unii ani.
"""

import argparse
import csv
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company, CompanyFinancial
from routers.company_utils import clean_text, parse_cui


FORM_PATTERNS = {
    "bl_bs_sl": re.compile(r"web_?bl_?bs_?sl_?(?:an_?)?(\d{4})", re.IGNORECASE),
    "uu": re.compile(r"web_?uu_?(?:an_?)?(\d{4})", re.IGNORECASE),
    "ir": re.compile(r"web_?ir_?(?:an_?)?(\d{4})", re.IGNORECASE),
}
BARE_YEAR_PATTERN = re.compile(r"^web(\d{4})\.txt$", re.IGNORECASE)

# Folders known to be empty/duplicate leftovers in the downloaded dataset.
EXCLUDED_DIR_NAMES = {"situatii_financiare_2023", "situatii_financiare_2024"}


def _normalize_label(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


# label normalizata -> camp direct din CompanyFinancial
CANONICAL_FIELDS = {
    "active imobilizate total": "active_imobilizate_total",
    "active circulante total din care": "active_circulante_total",
    "stocuri": "stocuri",
    "stocuri total": "stocuri",
    "creante": "creante",
    "casa si conturi la banci": "casa_conturi",
    "datorii": "datorii",
    "provizioane": "provizioane",
    "capitaluri total din care": "capitaluri_total",
    "capital": "capital_social",
    "capital subscris varsat": "capital_social",
    "patrimoniul public": "patrimoniul_public",
    "patrimoniul regiei": "patrimoniul_regiei",
    "cifra de afaceri neta": "cifra_afaceri",
    "venituri totale": "venituri_totale",
    "cheltuieli totale": "cheltuieli_totale",
    "numar mediu de salariati": "numar_salariati",
}

# label normalizata -> (camp semnat, semn). Profit si pierdere se combina intr-o
# singura valoare semnata (pierderea devine negativa).
SIGNED_FIELDS = {
    "profit brut": ("profit_brut", 1),
    "profitul brut": ("profit_brut", 1),
    "pierdere bruta": ("profit_brut", -1),
    "profit net": ("profit_net", 1),
    "profitul net": ("profit_net", 1),
    "pierdere neta": ("profit_net", -1),
}

FINANCIAL_COLUMNS = [
    "cifra_afaceri",
    "venituri_totale",
    "cheltuieli_totale",
    "profit_brut",
    "profit_net",
    "capitaluri_total",
    "capital_social",
    "active_imobilizate_total",
    "active_circulante_total",
    "stocuri",
    "creante",
    "casa_conturi",
    "datorii",
    "provizioane",
    "patrimoniul_public",
    "patrimoniul_regiei",
    "numar_salariati",
]


@dataclass
class SourceFile:
    data: Path | None = None
    legend: Path | None = None
    data_candidates: list[Path] = field(default_factory=list)
    legend_candidates: list[Path] = field(default_factory=list)


def _pick_best(paths: list[Path], year: int) -> Path:
    year_str = str(year)
    matches = [p for p in paths if year_str in p.parent.name]
    pool = matches if matches else paths
    return max(pool, key=lambda p: (p.stat().st_size, str(p)))


def discover_sources(root: Path) -> dict[int, dict[str, SourceFile]]:
    """Scan root recursively and group matching files by year and form type."""
    found: dict[tuple[int, str], list[Path]] = {}
    bare_by_year: dict[int, list[Path]] = {}

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.parent.name in EXCLUDED_DIR_NAMES:
            continue
        if path.suffix.lower() not in (".txt", ".csv"):
            continue

        name = path.name
        matched = False
        for form, pattern in FORM_PATTERNS.items():
            m = pattern.search(name)
            if m:
                year = int(m.group(1))
                found.setdefault((year, form), []).append(path)
                matched = True
                break

        if not matched:
            bm = BARE_YEAR_PATTERN.match(name)
            if bm:
                bare_by_year.setdefault(int(bm.group(1)), []).append(path)

    result: dict[int, dict[str, SourceFile]] = {}
    for (year, form), paths in found.items():
        data_candidates = [p for p in paths if p.suffix.lower() == ".txt"]
        legend_candidates = [p for p in paths if p.suffix.lower() == ".csv"]
        src = result.setdefault(year, {}).setdefault(form, SourceFile())
        src.data_candidates = sorted(data_candidates)
        src.legend_candidates = sorted(legend_candidates)
        src.data = _pick_best(data_candidates, year) if data_candidates else None
        src.legend = _pick_best(legend_candidates, year) if legend_candidates else None

    # Fallback for years where the BL_BS_SL data file has no dedicated name
    # (e.g. 2008: "web2008.txt", legend "webblbsslan2008.csv").
    for year, forms in result.items():
        src = forms.get("bl_bs_sl")
        if src is not None and src.data is None and src.legend is not None:
            candidates = bare_by_year.get(year, [])
            if candidates:
                src.data_candidates = sorted(candidates)
                src.data = _pick_best(candidates, year)

    return result


def _read_legend(legend_path: Path) -> dict[str, str | tuple[str, int]]:
    """Returns code (e.g. 'i13') -> field name, or (field, sign) for signed fields."""
    code_to_field: dict[str, str | tuple[str, int]] = {}
    unknown_labels: set[str] = set()

    with legend_path.open("r", encoding="cp1250", errors="replace", newline="") as handle:
        reader = csv.reader(handle, delimiter=";")
        for row in reader:
            if len(row) < 2:
                continue
            label_norm = _normalize_label(row[0])
            code_norm = row[1].strip().lower()
            if not code_norm or code_norm in ("cui", "caen"):
                continue
            if code_norm.isdigit():
                # Some legend files (e.g. web_ir_*.csv) drop the "i" prefix by mistake.
                code_norm = f"i{code_norm}"

            if label_norm in CANONICAL_FIELDS:
                code_to_field[code_norm] = CANONICAL_FIELDS[label_norm]
            elif label_norm in SIGNED_FIELDS:
                code_to_field[code_norm] = SIGNED_FIELDS[label_norm]
            else:
                unknown_labels.add(label_norm)

    if unknown_labels:
        print(
            f"  [avertisment] etichete necunoscute in {legend_path.name} (ignorate): "
            f"{sorted(unknown_labels)}",
            file=sys.stderr,
        )
    return code_to_field


def _build_column_map(header: list[str], code_to_field: dict) -> dict[int, str | tuple[str, int]]:
    column_map: dict[int, str | tuple[str, int]] = {}
    for idx, col_name in enumerate(header):
        code_norm = col_name.strip().lower()
        if code_norm in ("cui", "caen"):
            continue
        mapped = code_to_field.get(code_norm)
        if mapped is not None:
            column_map[idx] = mapped
    return column_map


def _parse_int(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        return int(round(float(value)))
    except ValueError:
        return None


def _iter_rows(data_path: Path, column_map: dict[int, str | tuple[str, int]], an: int, sursa: str):
    with data_path.open("r", encoding="latin-1", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            return
        cui_idx = next((i for i, c in enumerate(header) if c.strip().lower() == "cui"), 0)
        caen_idx = next((i for i, c in enumerate(header) if c.strip().lower() == "caen"), 1)

        for row in reader:
            if not row:
                continue
            cui = parse_cui(row[cui_idx]) if cui_idx < len(row) else None
            if cui is None:
                continue

            payload: dict[str, int | None] = {col: None for col in FINANCIAL_COLUMNS}
            signed_acc: dict[str, int] = {}
            signed_seen: dict[str, bool] = {}

            for idx, mapped in column_map.items():
                if idx >= len(row):
                    continue
                parsed = _parse_int(row[idx])
                if parsed is None:
                    continue
                if isinstance(mapped, tuple):
                    field_name, sign = mapped
                    signed_acc[field_name] = signed_acc.get(field_name, 0) + sign * parsed
                    signed_seen[field_name] = True
                else:
                    payload[mapped] = parsed

            for field_name, seen in signed_seen.items():
                if seen:
                    payload[field_name] = signed_acc[field_name]

            yield {
                "cui": cui,
                "caen": clean_text(row[caen_idx]) if caen_idx < len(row) else None,
                "an": an,
                "sursa": sursa,
                **payload,
            }


@dataclass
class ImportStats:
    rows_seen: int = 0
    inserted: int = 0
    updated: int = 0
    skipped_no_company: int = 0
    errors: int = 0

    def merge(self, other: "ImportStats") -> None:
        self.rows_seen += other.rows_seen
        self.inserted += other.inserted
        self.updated += other.updated
        self.skipped_no_company += other.skipped_no_company
        self.errors += other.errors


def _upsert_batch(batch: list[dict]) -> ImportStats:
    stats = ImportStats(rows_seen=len(batch))
    if not batch:
        return stats

    with SessionLocal() as session:
        cuis = {row["cui"] for row in batch}
        company_ids = dict(
            session.execute(select(Company.cui, Company.id).where(Company.cui.in_(cuis))).all()
        )

        payload_by_company: dict[tuple[int, int], dict] = {}
        for row in batch:
            company_id = company_ids.get(row["cui"])
            if company_id is None:
                stats.skipped_no_company += 1
                continue
            entry = {k: v for k, v in row.items() if k != "cui"}
            entry["company_id"] = company_id
            # A CUI can appear more than once in a single source file (e.g. amended
            # filings); ON CONFLICT DO UPDATE cannot touch the same row twice within
            # one statement, so keep only the last occurrence per (company_id, an).
            payload_by_company[(company_id, entry["an"])] = entry
        payload = list(payload_by_company.values())

        if not payload:
            return stats

        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            stmt = pg_insert(CompanyFinancial).values(payload)
            update_cols = {"sursa": stmt.excluded.sursa, "caen": stmt.excluded.caen}
            for col in FINANCIAL_COLUMNS:
                update_cols[col] = getattr(stmt.excluded, col)
            stmt = stmt.on_conflict_do_update(
                index_elements=[CompanyFinancial.company_id, CompanyFinancial.an],
                set_=update_cols,
            )
            session.execute(stmt)
            stats.inserted += len(payload)
        else:
            for row in payload:
                existing = (
                    session.query(CompanyFinancial)
                    .filter(
                        CompanyFinancial.company_id == row["company_id"],
                        CompanyFinancial.an == row["an"],
                    )
                    .one_or_none()
                )
                if existing is None:
                    session.add(CompanyFinancial(**row))
                    stats.inserted += 1
                else:
                    for key, value in row.items():
                        setattr(existing, key, value)
                    stats.updated += 1
        session.commit()
    return stats


def import_year_form(
    data_path: Path, legend_path: Path, an: int, sursa: str, batch_size: int
) -> ImportStats:
    code_to_field = _read_legend(legend_path)
    header = data_path.open("r", encoding="latin-1", errors="replace").readline()
    header_cols = header.strip().split(",")
    column_map = _build_column_map(header_cols, code_to_field)

    total = ImportStats()
    batch: list[dict] = []
    for row in _iter_rows(data_path, column_map, an, sursa):
        batch.append(row)
        if len(batch) >= batch_size:
            total.merge(_upsert_batch(batch))
            batch = []
    if batch:
        total.merge(_upsert_batch(batch))
    return total


def _parse_years(spec: str) -> list[int]:
    years: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            years.update(range(int(start), int(end) + 1))
        else:
            years.add(int(part))
    return sorted(years)


def print_discovery(sources: dict[int, dict[str, SourceFile]], years: list[int]) -> None:
    for year in years:
        forms = sources.get(year, {})
        print(f"=== {year} ===")
        for form in ("bl_bs_sl", "uu", "ir"):
            src = forms.get(form)
            if src is None or (src.data is None and src.legend is None):
                print(f"  {form:10s}: (negasit)")
                continue
            data_str = str(src.data) if src.data else "LIPSA"
            legend_str = str(src.legend) if src.legend else "LIPSA"
            print(f"  {form:10s}: data={data_str}")
            print(f"  {'':10s}  legenda={legend_str}")
            if len(src.data_candidates) > 1:
                print(f"  {'':10s}  alte candidate date: {[str(p) for p in src.data_candidates]}")
            if len(src.legend_candidates) > 1:
                print(f"  {'':10s}  alte candidate legenda: {[str(p) for p in src.legend_candidates]}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import indicatori financiari anuali MFP (situatii financiare) in PostgreSQL"
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parents[1] / "temp" / "mfp"),
        help="Radacina in care se cauta folderele situatii_financiare_*",
    )
    parser.add_argument("--years", required=True, help="Ex: 2025 sau 2008-2025 sau 2020,2022,2024")
    parser.add_argument("--dry-run", action="store_true", help="Doar afiseaza maparea an->fisiere")
    parser.add_argument("--truncate-year", type=int, help="Sterge datele existente pentru un an inainte de import")
    parser.add_argument("--batch-size", type=int, default=5000)
    args = parser.parse_args()

    root = Path(args.root)
    years = _parse_years(args.years)
    sources = discover_sources(root)

    if args.dry_run:
        print_discovery(sources, years)
        return

    init_postgres()

    if args.truncate_year is not None:
        with SessionLocal() as session:
            session.execute(
                delete(CompanyFinancial).where(CompanyFinancial.an == args.truncate_year)
            )
            session.commit()

    grand_total = ImportStats()
    for year in years:
        forms = sources.get(year, {})
        for form in ("bl_bs_sl", "uu", "ir"):
            src = forms.get(form)
            if src is None or src.data is None or src.legend is None:
                print(f"[{year}/{form}] sarit - fisier de date sau legenda negasit")
                continue
            print(f"[{year}/{form}] import din {src.data.name} (legenda {src.legend.name})")
            stats = import_year_form(src.data, src.legend, year, form, args.batch_size)
            grand_total.merge(stats)
            print(
                f"  randuri: {stats.rows_seen}, inserate/actualizate: {stats.inserted + stats.updated}, "
                f"fara companie: {stats.skipped_no_company}"
            )

    print("--- Total ---")
    print(f"Randuri citite: {grand_total.rows_seen}")
    print(f"Inserate/actualizate: {grand_total.inserted + grand_total.updated}")
    print(f"Sarite (CUI negasit in companies): {grand_total.skipped_no_company}")


if __name__ == "__main__":
    main()
