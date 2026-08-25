"""
Inițializare tabele SIRUTA în baza de date SQLite din fișierul siruta_cu_diacritice.csv.

Schema:
  judete     – județe (41 județe + municipiul București)
  localitati – toate UAT-urile: municipii, orașe, comune, sectoare

Tipuri UAT (tip_cod / tip_abrev):
  11 / CJ – Consiliu Județean   (rând administrativ, nu o localitate propriu-zisă)
  12 / M  – Municipiu
  13 / O  – Oraș
  14 / C  – Comună
  15 / B  – Municipiul București
  16 / S  – Sector (București)

Rulează independent față de init_caen_db.py; ambele scriu în același SQLITE_DB.

init_siruta_extins() adaugă, aditiv (nu atinge judete/localitati de mai sus), regiuni de
dezvoltare (NUTS2), abrevierea auto + codul SIRUTA propriu al fiecarui judet, si
localitati_componente (sate/localitati componente ale UAT-urilor, nivel NIV=3 in SIRUTA
oficial) -- din temp/SIRUTA_an_2025/SIRUTA.csv si JUDET.DBF, surse mai bogate decat
siruta_cu_diacritice.csv dar cu propriile coduri TIP (oficiale, 1-11/40/41), care se
suprapun numeric cu schema simplificata TIP_DENUMIRE de mai sus -- de aceea localitati
componente e tabela separata, nu adaugata in `localitati`. Trebuie rulat dupa init_siruta().
"""
import csv
import os
import re
import sqlite3

SQLITE_DB = os.getenv("SQLITE_DB", "caen.db")
CSV_PATH = os.path.join(os.path.dirname(__file__), "..","temp", "siruta_cu_diacritice.csv")
SIRUTA_EXTINS_DIR = os.path.join(os.path.dirname(__file__), "..", "temp", "SIRUTA_an_2025")
SIRUTA_EXTINS_CSV_PATH = os.path.join(SIRUTA_EXTINS_DIR, "SIRUTA.csv")
JUDET_DBF_PATH = os.path.join(SIRUTA_EXTINS_DIR, "JUDET.DBF")

TIP_DENUMIRE: dict[str, str] = {
    "11": "Consiliu Județean",
    "12": "Municipiu",
    "13": "Oraș",
    "14": "Comună",
    "15": "Municipiul București",
    "16": "Sector",
}

# Coduri TIP oficiale SIRUTA pentru localitati_componente (NIV=3 in SIRUTA.csv). Confirmate
# empiric prin esantionare de randuri reale (nu dintr-o legenda oficiala -- Metodologie.doc
# e format .doc binar, nu a putut fi citit). 11/19 sunt distinse de 10/18 pe baza distinctiei
# administrative reale dintre "sat apartinator" si "localitate componenta" pentru orase/
# municipii (comunele nu au aceasta distinctie -- doar 22=comuna insasi, 23=sat apartinator).
TIP_DENUMIRE_COMPONENTE: dict[int, str] = {
    6: "Sector al Municipiului București",
    9: "Municipiu reședință de județ (localitate de bază)",
    10: "Sat aparținător municipiu reședință de județ",
    11: "Localitate componentă (municipiu reședință de județ)",
    17: "Oraș (localitate de bază)",
    18: "Sat aparținător oraș",
    19: "Localitate componentă (oraș)",
    22: "Comună (localitate de bază)",
    23: "Sat aparținător comună",
}

# Regiuni de dezvoltare (NUTS2): codul REGIUNE din SIRUTA.csv -> (NUTS2, denumire). Corelatia
# REGIUNE<->NUTS a fost verificata pe date reale (fiecare judet dintr-o REGIUNE are acelasi
# prefix NUTS); denumirile sunt cele oficiale, standard, ale celor 8 regiuni.
REGIUNI: dict[int, tuple[str, str]] = {
    1: ("RO21", "Nord-Est"),
    2: ("RO22", "Sud-Est"),
    3: ("RO31", "Sud-Muntenia"),
    4: ("RO41", "Sud-Vest Oltenia"),
    5: ("RO42", "Vest"),
    6: ("RO11", "Nord-Vest"),
    7: ("RO12", "Centru"),
    8: ("RO32", "Bucuresti-Ilfov"),
}


def _normalize(name: str) -> str:
    """Elimină spațiile multiple și face trim."""
    return re.sub(r" {2,}", " ", name).strip()


def _strip_diacritics(value: str) -> str:
    import unicodedata

    return "".join(
        char for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )


def _norm_judet_name(value: str) -> str:
    """Nume de judet, comparabil intre surse: fara diacritice, majuscule, fara
    prefixul 'JUDETUL'/'MUNICIPIUL'/'MUN.' (Bucuresti apare ca "MUN. BUCURESTI" in
    tabela existenta, "MUNICIPIUL BUCURESTI" in SIRUTA.csv, "BUCURESTI" simplu in
    JUDET.DBF). Necesar pentru ca JUD (codul oficial SIRUTA) NU coincide cu
    cod_judet din tabela `judete` existenta pentru Calarasi/Giurgiu (51/52 in
    SIRUTA.csv si JUDET.DBF, dar 12/19 in siruta_cu_diacritice.csv) -- restul de
    40 de judete coincid numeric, dar nu ne putem baza pe asta, deci potrivim
    intotdeauna dupa nume, nu dupa JUD.
    """
    name = _strip_diacritics(_normalize(value)).upper()
    return re.sub(r"^(JUDETUL|MUNICIPIUL|MUN\.?) ", "", name)


def init_siruta() -> None:
    conn = sqlite3.connect(SQLITE_DB)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript("""
        DROP TABLE IF EXISTS localitati;
        DROP TABLE IF EXISTS judete;

        CREATE TABLE judete (
            cod_judet INTEGER PRIMARY KEY,
            denumire  TEXT NOT NULL
        );

        CREATE TABLE localitati (
            cod_siruta          INTEGER PRIMARY KEY,
            denumire            TEXT    NOT NULL,
            denumire_diacritice TEXT,
            tip_cod             INTEGER NOT NULL,
            tip_abrev           TEXT    NOT NULL,
            tip_denumire        TEXT    NOT NULL,
            cod_judet           INTEGER NOT NULL REFERENCES judete(cod_judet)
        );

        -- Indecși pentru căutare după nume, filtrare după județ și tip
        CREATE INDEX idx_localitati_denumire            ON localitati(denumire);
        CREATE INDEX idx_localitati_denumire_diacritice ON localitati(denumire_diacritice);
        CREATE INDEX idx_localitati_judet               ON localitati(cod_judet);
        CREATE INDEX idx_localitati_tip                 ON localitati(tip_cod);
    """)

    judete_vazute: set[int] = set()

    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cod_judet = int(row["cod_judet"])
            denumire_judet = _normalize(row["denumire_judet"])

            if cod_judet not in judete_vazute:
                judete_vazute.add(cod_judet)
                conn.execute(
                    "INSERT OR IGNORE INTO judete (cod_judet, denumire) VALUES (?, ?)",
                    (cod_judet, denumire_judet),
                )

            tip_cod_str = row["tip_uat_cod"].strip()
            tip_abrev = row["tip_uat_abrev"].strip()
            tip_denumire = TIP_DENUMIRE.get(tip_cod_str, tip_abrev)
            cod_siruta = int(row["cod_siruta"])
            denumire_uat = _normalize(row["denumire_uat"])
            denumire_diacritice = _normalize(row["denumire_uat_diacritice"]) or None

            conn.execute(
                """
                INSERT OR IGNORE INTO localitati
                    (cod_siruta, denumire, denumire_diacritice, tip_cod, tip_abrev, tip_denumire, cod_judet)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (cod_siruta, denumire_uat, denumire_diacritice, int(tip_cod_str), tip_abrev, tip_denumire, cod_judet),
            )

    conn.commit()

    count_j = conn.execute("SELECT COUNT(*) FROM judete").fetchone()[0]
    count_l = conn.execute("SELECT COUNT(*) FROM localitati").fetchone()[0]
    conn.close()
    print(f"SIRUTA incarcat: {count_j} judete, {count_l} localitati in '{SQLITE_DB}'")


def init_siruta_extins() -> None:
    from dbfread import DBF

    conn = sqlite3.connect(SQLITE_DB)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript("""
        DROP TABLE IF EXISTS localitati_componente;
        DROP TABLE IF EXISTS regiuni;

        CREATE TABLE regiuni (
            cod_regiune INTEGER PRIMARY KEY,
            denumire    TEXT NOT NULL,
            nuts2       TEXT NOT NULL UNIQUE
        );

        CREATE TABLE localitati_componente (
            cod_siruta         INTEGER PRIMARY KEY,
            denumire           TEXT    NOT NULL,
            denumire_ascii     TEXT    NOT NULL,
            tip_cod            INTEGER NOT NULL,
            tip_denumire       TEXT    NOT NULL,
            cod_siruta_parinte INTEGER NOT NULL REFERENCES localitati(cod_siruta),
            cod_judet          INTEGER NOT NULL REFERENCES judete(cod_judet)
        );

        CREATE INDEX idx_localitati_componente_parinte      ON localitati_componente(cod_siruta_parinte);
        CREATE INDEX idx_localitati_componente_judet        ON localitati_componente(cod_judet);
        CREATE INDEX idx_localitati_componente_denumire     ON localitati_componente(denumire);
        CREATE INDEX idx_localitati_componente_denumire_ascii ON localitati_componente(denumire_ascii);
    """)

    for cod_regiune, (nuts2, denumire) in REGIUNI.items():
        conn.execute(
            "INSERT INTO regiuni (cod_regiune, denumire, nuts2) VALUES (?, ?, ?)",
            (cod_regiune, denumire, nuts2),
        )

    # judete capata 3 coloane noi -- adaugate idempotent (ALTER TABLE ADD COLUMN da eroare daca
    # coloana exista deja, ceea ce s-ar intampla la a doua rulare a acestui script).
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(judete)")}
    for col_name, col_def in (
        ("cod_regiune", "cod_regiune INTEGER REFERENCES regiuni(cod_regiune)"),
        ("abbr", "abbr TEXT"),
        ("cod_siruta_judet", "cod_siruta_judet INTEGER"),
    ):
        if col_name not in existing_cols:
            conn.execute(f"ALTER TABLE judete ADD COLUMN {col_def}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_judete_abbr ON judete(abbr)")

    # cod_judet (tabela existenta) nu coincide numeric cu JUD (codul oficial SIRUTA) pentru
    # toate judetele -- vezi _norm_judet_name. Potrivim intotdeauna dupa nume normalizat.
    cod_judet_by_name = {
        _norm_judet_name(denumire): cod_judet
        for cod_judet, denumire in conn.execute("SELECT cod_judet, denumire FROM judete")
    }

    abbr_nepotrivite = 0
    for record in DBF(JUDET_DBF_PATH, encoding="cp1250"):
        cod_judet = cod_judet_by_name.get(_norm_judet_name(record["DENJ"]))
        if cod_judet is None:
            abbr_nepotrivite += 1
            continue
        conn.execute("UPDATE judete SET abbr = ? WHERE cod_judet = ?", (record["MNEMONIC"], cod_judet))

    with open(SIRUTA_EXTINS_CSV_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    # JUD (codul oficial din SIRUTA.csv) -> cod_judet (tabela existenta), rezolvat o singura
    # data prin randurile NIV=1 (cate un rand per judet, numele judetului e chiar DENLOC).
    jud_oficial_to_cod_judet: dict[str, int] = {}
    regiune_nepotrivite = 0
    for row in rows:
        if row["NIV"] != "1":
            continue
        cod_judet = cod_judet_by_name.get(_norm_judet_name(row["DENLOC"]))
        if cod_judet is None:
            regiune_nepotrivite += 1
            continue
        jud_oficial_to_cod_judet[row["JUD"]] = cod_judet
        conn.execute(
            "UPDATE judete SET cod_regiune = ?, cod_siruta_judet = ? WHERE cod_judet = ?",
            (int(row["REGIUNE"]), int(row["SIRUTA"]), cod_judet),
        )

    # SIRSUP ar trebui sa indice mereu spre un rand NIV=2 deja prezent in `localitati`, dar
    # verificam explicit ca sa nu crape tot scriptul pe un eventual rand orfan din sursa.
    localitati_valide = {
        row[0] for row in conn.execute("SELECT cod_siruta FROM localitati")
    }

    componente_orfane = 0
    for row in rows:
        if row["NIV"] != "3":
            continue
        cod_siruta_parinte = int(row["SIRSUP"])
        cod_judet = jud_oficial_to_cod_judet.get(row["JUD"])
        if cod_siruta_parinte not in localitati_valide or cod_judet is None:
            componente_orfane += 1
            continue
        tip_cod = int(row["TIP"])
        denumire = _normalize(row["DENLOC"])
        conn.execute(
            """
            INSERT OR IGNORE INTO localitati_componente
                (cod_siruta, denumire, denumire_ascii, tip_cod, tip_denumire, cod_siruta_parinte, cod_judet)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["SIRUTA"]),
                denumire,
                _strip_diacritics(denumire).upper(),
                tip_cod,
                TIP_DENUMIRE_COMPONENTE.get(tip_cod, f"Tip {tip_cod}"),
                cod_siruta_parinte,
                cod_judet,
            ),
        )

    conn.commit()

    count_r = conn.execute("SELECT COUNT(*) FROM regiuni").fetchone()[0]
    count_abbr = conn.execute("SELECT COUNT(*) FROM judete WHERE abbr IS NOT NULL").fetchone()[0]
    count_c = conn.execute("SELECT COUNT(*) FROM localitati_componente").fetchone()[0]
    conn.close()
    avertismente = abbr_nepotrivite + regiune_nepotrivite + componente_orfane
    print(
        f"SIRUTA extins incarcat: {count_r} regiuni, {count_abbr}/42 judete cu abrevieri, "
        f"{count_c} localitati componente in '{SQLITE_DB}'"
        + (
            f" (avertismente: {abbr_nepotrivite} abrevieri nepotrivite, "
            f"{regiune_nepotrivite} judete NIV=1 nepotrivite, {componente_orfane} componente orfane sarite)"
            if avertismente
            else ""
        )
    )


if __name__ == "__main__":
    init_siruta()
    init_siruta_extins()
