"""Rezolva cod_siruta pentru randurile din localitati_geo (coordonate, sursa PostGIS,
fara coduri SIRUTA) potrivind dupa (nume localitate normalizat, judet normalizat) fata
de `localitati` (nivel UAT) si `localitati_componente` (sate, nivel NIV=3).

De ce e nevoie de un script separat: localitati_geo nu are nicio coloana in comun cu
sursele SIRUTA (natcode arata a cod dar nu e -- verificat manual, ex. Alba Iulia are
natcode=1026 dar cod_siruta real 1017). Potrivirea dupa nume e ~99.6% fiabila la scara
completa (verificat), dar ~5.2% din randuri au acelasi nume in acelasi judet sub UAT-uri
parinte diferite (ex. mai multe sate "Valea Mare" in judete diferite comune) -- ambigue,
nu pot fi rezolvate doar din (nume, judet), asa ca raman NULL in loc sa fie ghicite.

Trebuie rulat dupa init_siruta_db.py (init_siruta() + init_siruta_extins()) si dupa
init_localitati_geo_db.py, de fiecare data cand localitati_geo e reincarcat de la zero
(coloana cod_siruta nu supravietuieste unui re-fetch din PostGIS).

Usage:
    python scripts/match_localitati_geo_siruta.py
"""
import os
import re
import sqlite3
import unicodedata
from collections import defaultdict

SQLITE_DB = os.getenv("SQLITE_DB", "caen.db")


def _strip_diacritics(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )


def _norm(value: str) -> str:
    return _strip_diacritics(re.sub(r" {2,}", " ", value).strip()).upper()


def match_localitati_geo_siruta() -> tuple[int, int, int]:
    """Returneaza (potrivite, ambigue, nepotrivite).

    UAT-urile (localitati) au prioritate fata de componente (sate) la acelasi
    (nume, judet): fiecare municipiu/oras/comuna resedinta are un rand "de sine" in
    localitati_componente (TIP 9/17/22) cu exact acelasi nume ca UAT-ul parinte -- nu e
    o ambiguitate reala, doar granularitate SIRUTA. La fel, multe comune au un sat cu
    acelasi nume ca insasi comuna (sat resedinta). In ambele cazuri UAT-ul e tinta corecta
    pentru un rand din localitati_geo. Doar cand NU exista un UAT cu acel nume si exista
    mai multe componente candidate (sate cu acelasi nume in judete diferite comune) e
    o ambiguitate reala, nerezolvabila din (nume, judet) singure.
    """
    conn = sqlite3.connect(SQLITE_DB)

    uat: dict[tuple[str, str], int] = {}
    for cod_siruta, denumire, judet_denumire in conn.execute("""
        SELECT l.cod_siruta, l.denumire, j.denumire
        FROM localitati l JOIN judete j ON l.cod_judet = j.cod_judet
    """):
        uat[(_norm(denumire), _norm(judet_denumire))] = cod_siruta

    componente: dict[tuple[str, str], list[int]] = defaultdict(list)
    for cod_siruta, denumire, judet_denumire in conn.execute("""
        SELECT lc.cod_siruta, lc.denumire, j.denumire
        FROM localitati_componente lc JOIN judete j ON lc.cod_judet = j.cod_judet
    """):
        componente[(_norm(denumire), _norm(judet_denumire))].append(cod_siruta)

    potrivite = ambigue = nepotrivite = 0
    for gid, nume_uat, judet in conn.execute("SELECT gid, nume_uat, judet FROM localitati_geo"):
        key = (_norm(nume_uat), _norm(judet))
        cod_siruta = uat.get(key)
        if cod_siruta is None:
            candidati_comp = componente.get(key)
            if not candidati_comp:
                nepotrivite += 1
                continue
            if len(candidati_comp) > 1:
                ambigue += 1
                continue
            cod_siruta = candidati_comp[0]
        conn.execute("UPDATE localitati_geo SET cod_siruta = ? WHERE gid = ?", (cod_siruta, gid))
        potrivite += 1

    conn.commit()
    conn.close()
    return potrivite, ambigue, nepotrivite


def main() -> None:
    potrivite, ambigue, nepotrivite = match_localitati_geo_siruta()
    total = potrivite + ambigue + nepotrivite
    print(
        f"Potrivire localitati_geo -> SIRUTA: {potrivite}/{total} rezolvate, "
        f"{ambigue} ambigue (nume duplicat in acelasi judet), {nepotrivite} nepotrivite -- "
        f"toate lasate NULL in loc sa fie ghicite."
    )


if __name__ == "__main__":
    main()
