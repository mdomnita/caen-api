"""Actualizeaza firmele existente din PostgreSQL folosind un snapshot ONRC.

Scop
----
Acest script este varianta ``update-only`` a ``scripts/import_companies.py``.
Primeste un fisier ONRC ``od_firme.csv`` si actualizeaza numai companiile al
caror CUI exista deja in tabela ``companies``. Companiile noi sunt ignorate si
trebuie adaugate separat cu ``import_companies.py``.

Flux de procesare
-----------------
1. Verifica existenta fisierului si valoarea dimensiunii batch-ului.
2. Initializeaza/verifica schema PostgreSQL.
3. Citeste fisierul in batch-uri, cu separatorul ``^``.
4. Reutilizeaza parserul importului pentru curatarea textului, validarea CUI,
   interpretarea datei si normalizarea numelui firmei.
5. Incarca firmele existente printr-o singura interogare pentru fiecare batch.
6. Compara payload-ul ONRC cu valorile stocate si scrie numai campurile care
   s-au schimbat.
7. Salveaza fiecare batch intr-o tranzactie separata si afiseaza statistici.

Efecte asupra datelor
---------------------
Sunt actualizate numai campurile provenite din ``od_firme.csv``. Informatiile
din alte surse, precum starea firmei, codurile CAEN si datele financiare, nu
sunt atinse. Daca se modifica o componenta a adresei, datele de geocodare sunt
golite implicit, deoarece coordonatele vechi pot indica o adresa incorecta.
Acest comportament poate fi dezactivat cu ``--keep-geocoding``.

Optiuni importante
------------------
``--dry-run``
    Calculeaza modificarile si statisticile fara sa scrie in baza de date.
``--keep-geocoding``
    Pastreaza coordonatele chiar daca adresa s-a schimbat.
``--batch-size``
    Controleaza numarul de randuri procesate intr-o tranzactie.

Exemple
-------
Simulare recomandata inaintea actualizarii::

    python scripts/update_companies.py --file temp/onrc/od_firme.csv --dry-run

Actualizare efectiva::

    python scripts/update_companies.py --file temp/onrc/od_firme.csv

La final sunt raportate randurile citite, firmele actualizate sau neschimbate,
CUI-urile inexistente, duplicatele, erorile de parsare si geocodarile
invalidate.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from routers.company_database import SessionLocal, init_postgres
from routers.company_models import Company
from scripts.import_companies import _read_batches


# Campurile folosite la construirea adresei pentru geocodare. Orice diferenta
# in aceasta lista face potential invalide coordonatele calculate anterior.
ADDRESS_FIELDS = frozenset(
    {
        "country", "county", "locality", "street", "street_number", "building",
        "staircase", "floor", "apartment", "postal_code", "sector", "address_extra",
    }
)
# Rezultatul si metadatele unei geocodari sunt resetate impreuna; pastrarea
# partiala a lor ar putea descrie in mod eronat coordonatele ca fiind valide.
GEOCODING_FIELDS = ("latitude", "longitude", "geocode_score", "geocode_status", "geocoded_at")


@dataclass
class UpdateStats:
    """Contoare cumulate pentru toate batch-urile citite din fisier."""

    rows_seen: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    duplicates: int = 0
    errors: int = 0
    geocodes_invalidated: int = 0

    def merge(self, other: "UpdateStats") -> None:
        """Adauga statisticile unui batch la totalul rularii."""

        self.rows_seen += other.rows_seen
        self.updated += other.updated
        self.unchanged += other.unchanged
        self.skipped += other.skipped
        self.duplicates += other.duplicates
        self.errors += other.errors
        self.geocodes_invalidated += other.geocodes_invalidated


def _changed_fields(company: Company, row: dict) -> set[str]:
    """Returneaza campurile ONRC ale caror valori difera de cele stocate."""

    return {key for key, value in row.items() if getattr(company, key) != value}


def _update_existing_batch(
    batch: list[dict],
    stats: UpdateStats,
    *,
    dry_run: bool = False,
    reset_geocoding: bool = True,
) -> UpdateStats:
    """Proceseaza un batch deja validat.

    ``updated`` numara firmele cu cel putin un camp diferit, iar ``unchanged``
    firmele identice cu snapshot-ul. In modul dry-run se executa comparatiile,
    dar obiectele SQLAlchemy nu sunt modificate si tranzactia nu este salvata.
    """

    if not batch:
        return stats

    with SessionLocal() as session:
        # O singura interogare per batch evita cate un SELECT pentru fiecare rand.
        existing_companies = {
            company.cui: company
            for company in session.scalars(
                select(Company).where(Company.cui.in_([row["cui"] for row in batch]))
            )
        }

        for row in batch:
            existing = existing_companies.get(row["cui"])
            if existing is None:
                # Acest script este update-only; firmele noi se importa separat.
                stats.skipped += 1
                continue

            changed_fields = _changed_fields(existing, row)
            if not changed_fields:
                stats.unchanged += 1
                continue

            stats.updated += 1
            address_changed = bool(changed_fields & ADDRESS_FIELDS)
            has_geocoding_data = any(getattr(existing, key) is not None for key in GEOCODING_FIELDS)
            if address_changed and reset_geocoding and has_geocoding_data:
                stats.geocodes_invalidated += 1

            if dry_run:
                continue

            # ``row`` contine exclusiv campurile construite de _row_to_payload(),
            # deci nu suprascrie id-ul sau datele provenite din celelalte surse.
            for key in changed_fields:
                setattr(existing, key, row[key])

            if address_changed and reset_geocoding:
                # Impiedica endpointul /coordonate sa serveasca pozitia adresei
                # vechi si permite o noua geocodare bulk sau live.
                for key in GEOCODING_FIELDS:
                    setattr(existing, key, None)

        # Fiecare batch este propria tranzactie: o eroare ulterioara nu anuleaza
        # batch-urile deja salvate si rularea poate fi reluata in siguranta.
        if not dry_run:
            session.commit()
    return stats


def update_companies(
    file_path: Path,
    batch_size: int = 1000,
    *,
    dry_run: bool = False,
    reset_geocoding: bool = True,
) -> UpdateStats:
    """Parcurge un export ONRC si actualizeaza numai CUIs deja existente.

    Args:
        file_path: Calea catre fisierul ``od_firme.csv``.
        batch_size: Numarul maxim de randuri brute citite intr-un batch.
        dry_run: Daca este adevarat, calculeaza rezultatul fara scrieri.
        reset_geocoding: Goleste geocodarea cand adresa se modifica.

    Returns:
        Statisticile cumulate pentru intregul fisier.

    Raises:
        ValueError: Daca ``batch_size`` nu este pozitiv.
        FileNotFoundError: Daca ``file_path`` nu indica un fisier existent.
    """

    if batch_size <= 0:
        raise ValueError("batch_size trebuie sa fie mai mare decat zero")
    if not file_path.is_file():
        raise FileNotFoundError(f"Fisierul sursa nu exista: {file_path}")

    # Creeaza/verifica schema PostgreSQL inainte de prima citire din fisier.
    init_postgres()

    total = UpdateStats()
    for batch, batch_stats in _read_batches(file_path, batch_size=batch_size):
        # Parserul comun contabilizeaza randurile invalide si duplicatele din
        # batch; aici se adauga rezultatul verificarii existentei in baza de date.
        stats = UpdateStats(
            rows_seen=batch_stats.rows_seen,
            duplicates=batch_stats.duplicates,
            errors=batch_stats.errors,
        )
        total.merge(
            _update_existing_batch(
                batch,
                stats,
                dry_run=dry_run,
                reset_geocoding=reset_geocoding,
            )
        )
    return total


def main() -> None:
    """Parseaza argumentele CLI, ruleaza actualizarea si afiseaza sumarul."""

    parser = argparse.ArgumentParser(
        description="Actualizeaza firmele ONRC deja existente in PostgreSQL "
        "(firmele fara CUI existent sunt sarite; pentru importul lor foloseste scripts/import_companies.py)"
    )
    parser.add_argument("--file", required=True, help="Calea catre fisierul sursa ONRC")
    parser.add_argument("--batch-size", type=int, default=1000, help="Numarul de randuri per batch")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculeaza si afiseaza modificarile fara a scrie in baza de date",
    )
    parser.add_argument(
        "--keep-geocoding",
        action="store_true",
        help="Pastreaza coordonatele existente chiar daca adresa firmei s-a schimbat",
    )
    args = parser.parse_args()

    file_path = Path(args.file)
    if args.batch_size <= 0:
        parser.error("--batch-size trebuie sa fie mai mare decat zero")
    if not file_path.is_file():
        parser.error(f"fisierul sursa nu exista: {file_path}")

    stats = update_companies(
        file_path,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        reset_geocoding=not args.keep_geocoding,
    )
    prefix = "Simulare finalizata" if args.dry_run else "Actualizare finalizata"
    print(f"{prefix}. Randuri citite: {stats.rows_seen}")
    print(f"Actualizate: {stats.updated}")
    print(f"Neschimbate: {stats.unchanged}")
    print(f"Sarite (CUI inexistent): {stats.skipped}")
    print(f"Duplicate in fisier: {stats.duplicates}")
    print(f"Erori / randuri ignorate: {stats.errors}")
    print(f"Coordonate invalidate dupa schimbarea adresei: {stats.geocodes_invalidated}")


if __name__ == "__main__":
    main()
