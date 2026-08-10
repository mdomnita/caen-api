"""Geocoding provider abstraction.

Extracted from scripts/geocode_companies.py so both the bulk company-geocoding
script and any future API endpoint (e.g. /coduripostale/rezolvare) share the
same ArcGIS client and proxy-rotation logic instead of duplicating it.
"""
import itertools
import sys
import threading
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from pydantic import BaseModel

from scripts.proxies import get_requests_proxy, proxy_list

ARCGIS_URL = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"


class GeocodeResult(BaseModel):
    lat: float
    lon: float
    score: float
    formatted_address: str
    provider: str


class GeocodingProvider(Protocol):
    def geocode(self, address: str) -> GeocodeResult | None: ...


class ArcGisProvider:
    """Free/anonymous ArcGIS World Geocoding Service.

    Anonymous use is rate-limited by ArcGIS; when use_proxy is True (and
    scripts/proxies.py has entries), requests are routed through a
    thread-safe rotating proxy pool to spread load across IPs.
    """

    def __init__(self, use_proxy: bool = True, timeout: float = 15.0):
        self._use_proxy = use_proxy and bool(proxy_list)
        self._timeout = timeout
        self._proxy_cycle = itertools.cycle(proxy_list) if proxy_list else None
        self._proxy_lock = threading.Lock()

    def _next_proxy(self) -> dict[str, str] | None:
        if self._proxy_cycle is None:
            return None
        with self._proxy_lock:
            proxy_dict = next(self._proxy_cycle)
        return get_requests_proxy(proxy_dict)

    def geocode(self, address: str) -> GeocodeResult | None:
        proxies = self._next_proxy() if self._use_proxy else None
        response = requests.get(
            ARCGIS_URL,
            params={
                "SingleLine": address,
                "f": "json",
                "outFields": "Score",
                "maxLocations": 1,
            },
            proxies=proxies,
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise requests.RequestException(f"ArcGIS a raspuns cu eroare: {data['error']}")
        candidates = data.get("candidates") or []
        if not candidates:
            return None
        best = candidates[0]
        location = best["location"]
        return GeocodeResult(
            lat=location["y"],
            lon=location["x"],
            score=best.get("score", 0.0),
            formatted_address=best.get("address", ""),
            provider="arcgis",
        )


def get_geocoding_provider() -> GeocodingProvider:
    """Shared FastAPI dependency: default geocoding provider for any router
    (routers/coduripostale.py, routers/companies.py, ...) that needs one.
    """
    return ArcGisProvider()
