#!/usr/bin/env python3
"""
Descarca toate fisierele CSV din seturile de date ale institutiilor ONRC, MFP
si Posta Romana de pe data.gov.ro (https://data.gov.ro/organization/onrc,
https://data.gov.ro/organization/mfp si
https://data.gov.ro/organization/posta-romana).

Parcurge paginile listei de seturi de date pentru fiecare organizatie, apoi
pentru fiecare set descarca toate resursele CSV intr-un subfolder propriu sub
temp/<organizatie>/.

Ruleaza din radacina repo-ului:
    python scripts/get_onrc_datasets.py
"""
import http.client
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

REPO_ROOT = Path(__file__).parent.parent
TEMP_DIR = REPO_ROOT / "temp"

BASE_URL = "https://data.gov.ro"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; caen-api-scripts/1.0)"}

ORGANIZATIONS = ["onrc", "mfp", "posta-romana","ancpi"]


def _get_dataset_slugs(session: requests.Session, org_url: str) -> list[str]:
    slugs: list[str] = []
    page = 1
    while True:
        resp = session.get(org_url, params={"page": page}, headers=HEADERS, timeout=30, verify=False)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        page_slugs = sorted({
            a["href"].removeprefix("/dataset/")
            for a in soup.select("a[href^='/dataset/']")
            if "/" not in a["href"].removeprefix("/dataset/")
        })
        if not page_slugs:
            break

        slugs.extend(page_slugs)
        print(f"Pagina {page}: {len(page_slugs)} seturi de date.")
        page += 1

    return slugs


def _get_csv_links(session: requests.Session, slug: str) -> list[str]:
    resp = session.get(f"{BASE_URL}/dataset/{slug}", headers=HEADERS, timeout=30, verify=False)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    links = {
        urljoin(BASE_URL, a["href"])
        for a in soup.select("a[href*='/download/']")
        if (a["href"].lower().endswith(".csv") or a["href"].lower().endswith(".xls") or a["href"].lower().endswith(".xlsx"))
    }
    return sorted(links)


def _download_csv(session: requests.Session, url: str, dest: Path) -> None:
    if dest.exists():
        print(f"    - {dest.name} (exista deja, sar peste)")
        return

    try:
        with session.get(url, headers=HEADERS, timeout=120, stream=True, verify=False) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
    except (requests.exceptions.RequestException, http.client.IncompleteRead):
        if dest.exists():
            dest.unlink()
        raise
    print(f"    - {dest.name} descarcat.")


def get_organization_datasets(session: requests.Session, org: str) -> None:
    org_url = f"{BASE_URL}/organization/{org}"
    org_dir = TEMP_DIR / org
    org_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== Organizatie: {org} ===")
    slugs = _get_dataset_slugs(session, org_url)
    print(f"\nTotal seturi de date gasite pentru {org}: {len(slugs)}\n")

    for i, slug in enumerate(slugs, start=1):
        print(f"[{i}/{len(slugs)}] {slug}")
        dataset_dir = org_dir / slug
        try:
            csv_links = _get_csv_links(session, slug)
        except requests.HTTPError as exc:
            print(f"    WARNING: nu s-a putut citi pagina setului de date ({exc}) — sar peste", file=sys.stderr)
            continue

        if not csv_links:
            print("    (niciun fisier CSV gasit)")
            continue

        dataset_dir.mkdir(parents=True, exist_ok=True)
        for url in csv_links:
            filename = url.rsplit("/", 1)[-1]
            try:
                _download_csv(session, url, dataset_dir / filename)
            except (requests.exceptions.RequestException, http.client.IncompleteRead) as exc:
                print(f"    WARNING: descarcare esuata pentru {url} ({exc})", file=sys.stderr)


def get_onrc_datasets() -> None:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    with requests.Session() as session:
        for org in ORGANIZATIONS:
            get_organization_datasets(session, org)

    print("\nFinalizat.")


if __name__ == "__main__":
    get_onrc_datasets()
