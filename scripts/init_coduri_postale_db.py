"""
Initializare tabela coduri_postale in baza de date SQLite, din fisierele CSV
(convertite din .xls) ale Postei Romane, publicate pe data.gov.ro:
https://data.gov.ro/dataset/coduri-postale-romania

Surse (varianta cu SIRUTA, cod_siruta e preluat direct din sursa, fara
potrivire dupa nume):
  infocod-mai-2016_siruta.csv       - strazi Bucuresti (are sector, nu are judet)
  infocod-mai-2016_orase_siruta.csv - localitati > 50.000 locuitori, nivel strada
  infocod-mai-2016_sate_siruta.csv  - localitati < 50.000 locuitori, doar nivel localitate

Rezolva cod_judet prin potrivire dupa nume (fara diacritice, uppercase) fata de
tabela `judete`, deci trebuie rulat dupa init_siruta_db.py (in aceeasi baza).

Rulează independent față de celelalte scripturi init_*; toate scriu în același SQLITE_DB.
"""
import csv
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.text_normalization import normalize_search, normalize_whitespace

SQLITE_DB = os.getenv("SQLITE_DB", "caen.db")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "temp", "posta-romana", "coduri-postale-romania")

BUCURESTI_CSV = os.path.join(DATA_DIR, "infocod-mai-2016_siruta.csv")
ORASE_CSV = os.path.join(DATA_DIR, "infocod-mai-2016_orase_siruta.csv")
SATE_CSV = os.path.join(DATA_DIR, "infocod-mai-2016_sate_siruta.csv")

BUCURESTI_COD_JUDET = 42
BUCURESTI_JUDET_RAW = "București"
BUCURESTI_LOCALITATE_RAW = "București"

_LOCALITATE_PARINTE_RE = re.compile(r"^(.*)\s+\((.*)\)$")
_NUMAR_TOKEN_RE = re.compile(r"^(\d+)([A-Z]?)(?:-(\d+|T)([A-Z]?))?$")


def _int_or_none(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


def _split_localitate(raw: str) -> tuple[str, str | None]:
    """Split a 'Localitate' cell into (localitate, parinte).

    Small 'sate' entries are sometimes suffixed with their parent comuna in
    parentheses, e.g. 'Lăzăreşti (Schitu Goleşti)' -> ('Lăzăreşti',
    'Schitu Goleşti'). Plain localities (no parentheses) return (raw, None).
    """
    raw = normalize_whitespace(raw)
    m = _LOCALITATE_PARINTE_RE.match(raw)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return raw, None


def _parse_nr_token(token: str) -> tuple[int, int | None, int, str | None] | None:
    """Parse a single already-unprefixed number/range token, e.g. '23-49',
    '105-T', or '21'. Returns (numar_min, numar_max, numar_open_ended,
    numar_paritate), or None if the token doesn't cleanly match (letter
    suffixes like '15A-31' are left unparsed on purpose).
    """
    m = _NUMAR_TOKEN_RE.match(token)
    if not m:
        return None

    low_s, low_suffix, high_s, high_suffix = m.groups()
    if low_suffix or high_suffix:
        return None

    low = int(low_s)
    if high_s is None:
        paritate = "impar" if low % 2 else "par"
        return low, low, 0, paritate
    if high_s == "T":
        paritate = "impar" if low % 2 else "par"
        return low, None, 1, paritate

    high = int(high_s)
    paritate = None
    if (low % 2) == (high % 2):
        paritate = "impar" if low % 2 else "par"
    return low, high, 0, paritate


def _parse_nr_group(group: str) -> tuple[int | None, int | None, int, str | None]:
    """Parse a single 'nr.'-type group's body (an optional leading 'nr.' is
    stripped; groups after the first ';' typically don't repeat it in the
    source). Multi-token (comma-separated) bodies are left unparsed.
    """
    body = group[3:].strip() if group.lower().startswith("nr.") else group.strip()
    tokens = [t.strip() for t in body.split(",") if t.strip()]
    if len(tokens) != 1:
        return None, None, 0, None
    parsed = _parse_nr_token(tokens[0])
    return parsed[:] if parsed else (None, None, 0, None)


def _empty_numar_entry(numar_raw: str | None, numar_tip: str | None = None) -> dict:
    return {
        "numar_raw": numar_raw,
        "numar_tip": numar_tip,
        "numar_min": None,
        "numar_max": None,
        "numar_open_ended": 0,
        "numar_paritate": None,
    }


def _split_bl_tokens(group: str) -> list[str]:
    """Split a 'bl.' cell's body into individual block tokens, e.g.
    'bl. 4, 20, 38' -> ['4', '20', '38']. Block identifiers are not numeric
    ranges (they can be roman numerals, letters, named blocks, etc.), so
    unlike 'nr.' tokens they are never parsed into numar_min/numar_max —
    only split, one DB row per token.
    """
    body = group[3:].strip() if group.lower().startswith("bl.") else group.strip()
    return [t.strip() for t in body.split(",") if t.strip()]


def _parse_numar_entries(raw: str | None) -> list[dict]:
    """Best-effort parse of the free-text 'Numar'/'Numar/Bloc' field into one
    or more entries — one per DB row to insert.

    Cases handled, in order:

    1. Empty/blank cell (e.g. some Bucuresti rows have no 'Numar' at all)
       -> a single entry with everything None; the row still gets inserted
       (the street/postal-code data is still valid, only the number is
       unknown).

    2. 'bl. <token>' with no commas, e.g. 'bl. XIII'
       -> a single as-is entry, numar_tip='bl', numeric fields left None.
       Block identifiers aren't numeric ranges, so there's nothing to parse
       here beyond recognizing the type.

    3. 'bl. <token>, <token>, ...' with commas, e.g.
       'bl. 4, 20, 38, 44, 60, 80, 90' or 'bl. II, IV, VI'
       -> one entry PER comma-separated token (numar_raw = just that token,
       e.g. '4', 'II'), all with numar_tip='bl' and numeric fields None.
       This is the one case in this function that turns a single source
       cell into multiple DB rows independently of the ';' splitting below.

    4. Anything not starting with 'nr.' or 'bl.' (rare/malformed source data)
       -> a single unparsed entry, numar_tip=None.

    5. 'nr. <range>' with no ';', e.g. 'nr. 1-21' or 'nr. 21-T'
       -> a single entry, parsed via _parse_nr_group/_parse_nr_token into
       numar_min/numar_max/numar_open_ended/numar_paritate. Multi-token
       (comma-separated) 'nr.' bodies are NOT split here (unlike 'bl.'
       above) — they stay a single unparsed entry, since a plain number
       list under 'nr.' hasn't come up in the source data yet.

    6. 'nr. <range>; <range>; ...', e.g. 'nr. 23-49; 2-90' or
       'nr. 105-T; 162-T'
       -> one entry PER ';'-separated group, each parsed independently
       (only the first group repeats the 'nr.' prefix in the source; later
       groups are bare ranges like '2-90'). min/max/paritate are derived
       per group from its own first/last number's parity — a cell can
       legitimately mix an odd sub-range with an even one.

    In every unparsed case, numar_raw is kept verbatim (or, for case 3, as
    the individual token) rather than guessing — callers can always fall
    back to displaying/searching the raw text.
    """
    raw = (raw or "").strip()
    if not raw:
        # Case 1: empty cell.
        return [_empty_numar_entry(None)]

    groups = [g.strip() for g in raw.split(";") if g.strip()]
    if not groups:
        return [_empty_numar_entry(raw)]

    first = groups[0]

    if first.lower().startswith("bl."):
        if len(groups) == 1:
            tokens = _split_bl_tokens(first)
            if len(tokens) > 1:
                # Case 3: comma-separated block list -> one row per token.
                return [_empty_numar_entry(token, "bl") for token in tokens]
        # Case 2 (single token) or a ';'-separated 'bl.' cell (not split
        # further here, unseen in the source so far) -> left as-is.
        return [_empty_numar_entry(raw, "bl")]

    if not first.lower().startswith("nr."):
        # Case 4: neither 'nr.' nor 'bl.' prefix.
        return [_empty_numar_entry(raw)]

    if len(groups) == 1:
        # Case 5: single 'nr.' range, no ';'.
        low, high, open_ended, paritate = _parse_nr_group(first)
        return [{
            "numar_raw": raw,
            "numar_tip": "nr",
            "numar_min": low,
            "numar_max": high,
            "numar_open_ended": open_ended,
            "numar_paritate": paritate,
        }]

    # Case 6: multiple ';'-separated 'nr.' ranges -> one row per group.
    entries = []
    for group in groups:
        low, high, open_ended, paritate = _parse_nr_group(group)
        entries.append({
            "numar_raw": group,
            "numar_tip": "nr",
            "numar_min": low,
            "numar_max": high,
            "numar_open_ended": open_ended,
            "numar_paritate": paritate,
        })
    return entries


def _load_judete_lookup(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT cod_judet, denumire FROM judete").fetchall()
    return {normalize_search(denumire): cod_judet for cod_judet, denumire in rows}


def init_coduri_postale() -> None:
    conn = sqlite3.connect(SQLITE_DB)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript("""
        DROP TABLE IF EXISTS coduri_postale;

        CREATE TABLE coduri_postale (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            cod_postal              TEXT    NOT NULL,

            judet_raw               TEXT,
            judet_norm               TEXT    NOT NULL,
            cod_judet                INTEGER,

            localitate_raw           TEXT,
            localitate_norm          TEXT    NOT NULL,
            localitate_parinte_raw   TEXT,
            localitate_parinte_norm  TEXT,
            cod_siruta                INTEGER,
            siruta_sirsup             INTEGER,
            siruta_niv                 INTEGER,

            sector                   INTEGER,

            tip_artera_raw           TEXT,
            tip_artera_norm          TEXT,
            strada_raw                TEXT,
            strada_norm                TEXT,

            numar_raw                 TEXT,
            numar_tip                  TEXT,
            numar_min                  INTEGER,
            numar_max                  INTEGER,
            numar_open_ended           INTEGER NOT NULL DEFAULT 0,
            numar_paritate              TEXT,

            oficiu_distribuire         TEXT,
            sursa                       TEXT    NOT NULL,
            sursa_versiune              TEXT    NOT NULL DEFAULT 'infocod-mai-2016'
        );

        CREATE INDEX idx_cp_cod_postal        ON coduri_postale(cod_postal);
        CREATE INDEX idx_cp_judet_norm        ON coduri_postale(judet_norm);
        CREATE INDEX idx_cp_localitate_norm   ON coduri_postale(localitate_norm);
        CREATE INDEX idx_cp_strada_norm       ON coduri_postale(strada_norm);
        CREATE INDEX idx_cp_cod_siruta        ON coduri_postale(cod_siruta);
        CREATE INDEX idx_cp_judet_localitate  ON coduri_postale(judet_norm, localitate_norm);
        CREATE INDEX idx_cp_localitate_strada ON coduri_postale(localitate_norm, strada_norm);
    """)

    judete_lookup = _load_judete_lookup(conn)

    stats = {"bucuresti": 0, "oras": 0, "sat": 0}
    cod_judet_resolved = 0
    cod_judet_missing: set[str] = set()
    cod_siruta_missing = 0

    insert_sql = """
        INSERT INTO coduri_postale (
            cod_postal, judet_raw, judet_norm, cod_judet,
            localitate_raw, localitate_norm, localitate_parinte_raw, localitate_parinte_norm,
            cod_siruta, siruta_sirsup, siruta_niv, sector,
            tip_artera_raw, tip_artera_norm, strada_raw, strada_norm,
            numar_raw, numar_tip, numar_min, numar_max, numar_open_ended, numar_paritate,
            oficiu_distribuire, sursa
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def resolve_cod_judet(judet_raw: str, judet_norm: str) -> int | None:
        nonlocal cod_judet_resolved
        cod_judet = judete_lookup.get(judet_norm)
        if cod_judet is not None:
            cod_judet_resolved += 1
        else:
            cod_judet_missing.add(judet_raw)
        return cod_judet

    rows_inserted = {"bucuresti": 0, "oras": 0, "sat": 0}

    # --- Bucuresti (strazi) ---------------------------------------------
    # This source file has no 'Judet'/'Localitate' columns at all (every row
    # is implicitly Bucuresti) and 'SIRUTA SECTOR' is sector-level, not
    # locality-level -- so judet/localitate are hardcoded constants here
    # instead of looked up, and 'Sector' (1-6) is stored on its own column.
    # One row in, possibly several rows out: _parse_numar_entries() may
    # split 'Numar' into multiple entries (see its docstring for the cases).
    with open(BUCURESTI_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            localitate_norm = normalize_search(BUCURESTI_LOCALITATE_RAW)
            tip_artera_raw = normalize_whitespace(row["Tip artera"])
            strada_raw = normalize_whitespace(row["Denumire artera"])
            cod_siruta = _int_or_none(row["SIRUTA SECTOR"])
            if cod_siruta is None:
                cod_siruta_missing += 1
            stats["bucuresti"] += 1

            for entry in _parse_numar_entries(row["Numar"]):
                conn.execute(insert_sql, (
                    row["Codpostal"], BUCURESTI_JUDET_RAW, normalize_search(BUCURESTI_JUDET_RAW), BUCURESTI_COD_JUDET,
                    BUCURESTI_LOCALITATE_RAW, localitate_norm, None, None,
                    cod_siruta, _int_or_none(row["SIRSUP"]), _int_or_none(row["NIV"]), _int_or_none(row["Sector"]),
                    tip_artera_raw, normalize_search(tip_artera_raw), strada_raw, normalize_search(strada_raw),
                    entry["numar_raw"], entry["numar_tip"], entry["numar_min"], entry["numar_max"],
                    entry["numar_open_ended"], entry["numar_paritate"],
                    row["Oficiu distribuire"] or None, "bucuresti",
                ))
                rows_inserted["bucuresti"] += 1

    # --- Orase (> 50.000 locuitori, nivel strada) ------------------------
    # Has real 'Judet'/'Localitate' columns (cod_judet resolved by name via
    # resolve_cod_judet()) and street-level detail like Bucuresti, so it
    # goes through the same _parse_numar_entries() splitting for 'Numar/Bloc'.
    # 'Localitate' can carry a '(parinte)' suffix here too, same as Sate
    # below, though it's rare at this population tier.
    with open(ORASE_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            judet_raw = normalize_whitespace(row["Judet"])
            judet_norm = normalize_search(judet_raw)
            cod_judet = resolve_cod_judet(judet_raw, judet_norm)

            localitate_raw, localitate_parinte_raw = _split_localitate(row["Localitate"])
            localitate_norm = normalize_search(localitate_raw)
            localitate_parinte_norm = normalize_search(localitate_parinte_raw) if localitate_parinte_raw else None

            tip_artera_raw = normalize_whitespace(row["Tip artera"])
            strada_raw = normalize_whitespace(row["Denumire artera"])
            cod_siruta = _int_or_none(row["SIRUTA"])
            if cod_siruta is None:
                cod_siruta_missing += 1
            stats["oras"] += 1

            for entry in _parse_numar_entries(row["Numar/Bloc"]):
                conn.execute(insert_sql, (
                    row["Codpostal"], judet_raw, judet_norm, cod_judet,
                    localitate_raw, localitate_norm, localitate_parinte_raw, localitate_parinte_norm,
                    cod_siruta, _int_or_none(row["SIRSUP"]), _int_or_none(row["NIV"]), None,
                    tip_artera_raw, normalize_search(tip_artera_raw), strada_raw, normalize_search(strada_raw),
                    entry["numar_raw"], entry["numar_tip"], entry["numar_min"], entry["numar_max"],
                    entry["numar_open_ended"], entry["numar_paritate"],
                    None, "oras",
                ))
                rows_inserted["oras"] += 1

    # --- Sate (< 50.000 locuitori, doar nivel localitate) ----------------
    # No street/number columns at all in this source file -- one row in,
    # exactly one row out (no _parse_numar_entries() call, numar_* fields
    # are always None/0 here). 'Localitate' frequently carries a
    # '(parinte)' suffix (a sat's parent comuna), handled by
    # _split_localitate().
    with open(SATE_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            judet_raw = normalize_whitespace(row["Judet"])
            judet_norm = normalize_search(judet_raw)
            cod_judet = resolve_cod_judet(judet_raw, judet_norm)

            localitate_raw, localitate_parinte_raw = _split_localitate(row["Localitate"])
            localitate_norm = normalize_search(localitate_raw)
            localitate_parinte_norm = normalize_search(localitate_parinte_raw) if localitate_parinte_raw else None

            cod_siruta = _int_or_none(row["SIRUTA"])
            if cod_siruta is None:
                cod_siruta_missing += 1

            conn.execute(insert_sql, (
                row["Codpostal"], judet_raw, judet_norm, cod_judet,
                localitate_raw, localitate_norm, localitate_parinte_raw, localitate_parinte_norm,
                cod_siruta, None, None, None,
                None, None, None, None,
                None, None, None, None, 0, None,
                None, "sat",
            ))
            stats["sat"] += 1
            rows_inserted["sat"] += 1

    conn.commit()

    total_sursa = sum(stats.values())
    total_rows = sum(rows_inserted.values())
    total_judet_rows = stats["oras"] + stats["sat"]
    conn.close()

    print(f"Coduri postale procesate: {total_sursa} randuri sursa ({stats['bucuresti']} bucuresti, {stats['oras']} orase, {stats['sat']} sate)")
    print(f"Coduri postale incarcate: {total_rows} randuri in '{SQLITE_DB}' "
          f"({rows_inserted['bucuresti']} bucuresti, {rows_inserted['oras']} orase, {rows_inserted['sat']} sate) "
          f"— mai multe decat randurile sursa acolo unde 'Numar'/'Numar/Bloc' avea mai multe intervale separate prin ';'")
    print(f"cod_judet rezolvat: {cod_judet_resolved}/{total_judet_rows}")
    if cod_judet_missing:
        print(f"cod_judet nerezolvat pentru judetele: {sorted(cod_judet_missing)}")
    print(f"cod_siruta lipsa in sursa: {cod_siruta_missing}/{total_sursa}")


if __name__ == "__main__":
    init_coduri_postale()
