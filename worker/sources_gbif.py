"""GBIF occurrence adapter.

Why GBIF and not a live Waarneming.nl scrape:
  * Observation.org publishes its Dutch records to GBIF and permits
    non-commercial reuse with attribution (CC BY-NC 4.0).
  * A direct site API is bot-protected and access-restricted; GBIF is stable,
    machine-readable and citable (DOI 10.15468/5nilie).

Dutch records in that dataset are generalised to ~5x5 km, so they can only
support broad hotspot evidence. This module never treats them as exact sites.
"""

from __future__ import annotations

import time
from typing import Callable, Iterable

import requests

from config import (
    GBIF_MAX_RECORDS_PER_TAXON,
    GBIF_PAGE_SIZE,
    HTTP_TIMEOUT_S,
    USER_AGENT,
)

GBIF = "https://api.gbif.org/v1"

# Licences we may use for a private, non-commercial derived product.
# NoDerivatives is excluded; NC is acceptable for this use.
ALLOWED_LICENCE_TOKENS = ("cc0", "cc-by", "by-nc")
BLOCKED_LICENCE_TOKENS = ("nd",)

Progress = Callable[[float, str, str], None]


def _noop(progress: float, phase: str, message: str) -> None:  # pragma: no cover
    pass


def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def licence_ok(licence: str | None) -> bool:
    if not licence:
        return False
    low = licence.lower()
    if any(tok in low for tok in BLOCKED_LICENCE_TOKENS):
        return False
    return any(tok in low for tok in ALLOWED_LICENCE_TOKENS)


def match_species(name: str) -> dict | None:
    """Resolve a scientific name to a GBIF backbone usage key."""
    try:
        r = requests.get(
            GBIF + "/species/match",
            params={"name": name, "kingdom": "Fungi", "strict": "false"},
            headers=_headers(),
            timeout=HTTP_TIMEOUT_S,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException:
        return None
    if not data.get("usageKey") or data.get("matchType") == "NONE":
        return None
    # Only accept a confident species-level match.
    if data.get("rank") != "SPECIES":
        return None
    return {
        "taxon_key": data["usageKey"],
        "scientific_name": data.get("scientificName") or data.get("canonicalName"),
        "rank": data.get("rank"),
        "status": data.get("status"),
    }


def fetch_occurrences(
    taxon_key: int,
    year_from: int = 2005,
    year_to: int = 2100,
    max_records: int | None = None,
    progress: Progress | None = None,
) -> Iterable[dict]:
    """Yield permitted Dutch occurrence records for one taxon and year range.

    Uses offset paging; GBIF caps deep paging, but per-taxon volumes for our
    curated taxa are well inside that, and we hard-cap anyway.
    """
    progress = progress or _noop
    cap = GBIF_MAX_RECORDS_PER_TAXON if max_records is None else max_records
    offset = 0
    fetched = 0
    while fetched < cap:
        params = {
            "taxonKey": taxon_key,
            "country": "NL",
            "hasCoordinate": "true",
            "hasGeospatialIssue": "false",
            "year": "{},{}".format(year_from, year_to),
            "limit": GBIF_PAGE_SIZE,
            "offset": offset,
        }
        for attempt in range(4):
            try:
                r = requests.get(
                    GBIF + "/occurrence/search",
                    params=params,
                    headers=_headers(),
                    timeout=HTTP_TIMEOUT_S,
                )
                if r.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
        data = r.json()
        results = data.get("results", [])
        if not results:
            return
        for rec in results:
            if not licence_ok(rec.get("license")):
                continue
            lat = rec.get("decimalLatitude")
            lon = rec.get("decimalLongitude")
            if lat is None or lon is None:
                continue
            yield {
                "source_id": str(rec.get("key")),
                "species_key": taxon_key,
                "observed_on": (rec.get("eventDate") or "")[:10] or None,
                "lat": float(lat),
                "lon": float(lon),
                "coord_uncertainty_m": rec.get("coordinateUncertaintyInMeters"),
                "generalized": bool(rec.get("dataGeneralizations")),
                "licence": rec.get("license"),
                "dataset_key": rec.get("datasetKey"),
                "recorded_by": rec.get("recordedBy"),
                "raw": {
                    "basisOfRecord": rec.get("basisOfRecord"),
                    "datasetKey": rec.get("datasetKey"),
                    "dataGeneralizations": rec.get("dataGeneralizations"),
                    "informationWithheld": rec.get("informationWithheld"),
                    "occurrenceStatus": rec.get("occurrenceStatus"),
                },
            }
            fetched += 1
            if fetched >= cap:
                return
        offset += GBIF_PAGE_SIZE
        progress(
            min(1.0, fetched / max(1, cap)),
            "fetch",
            "{} records".format(fetched),
        )
        if data.get("endOfRecords"):
            return
