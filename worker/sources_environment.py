"""Environmental context per coarse cell.

MVP sources (all open, all keyless):
  * Natura 2000 protection flag via PDOK OGC API Features;
  * habitat fractions and path density via OpenStreetMap Overpass.

These are *proxies*. OSM is volunteer-mapped (coverage varies), and neither
source identifies tree species, deadwood, soil pH or forest-floor moisture.
Cells that cannot be enriched keep neutral defaults and a lower confidence.

Overpass is queried per cell for cells that already have observation evidence,
so national volume stays bounded. Ways only: multipolygon relations are not
counted (documented limitation).
"""

from __future__ import annotations

import math
from typing import Callable

import requests

from config import HTTP_TIMEOUT_S, USER_AGENT

OVERPASS = "https://overpass-api.de/api/interpreter"
NATURA2000_ITEMS = "https://api.pdok.nl/rvo/natura2000/ogc/v1/collections/natura2000/items"

Progress = Callable[[float, str, str], None]

# Tags that make up each habitat fraction.
HABITAT_TAGS = {
    "forest": ['["natural"="wood"]', '["landuse"="forest"]'],
    "heath": ['["natural"="heath"]', '["natural"="scrub"]'],
    "wet_nature": ['["natural"="wetland"]', '["natural"="marsh"]'],
}
PATH_FILTER = '["highway"~"^(path|footway|track|bridleway|steps)$"]'

_M_PER_DEG_LAT = 111320.0


def _mpd_lon(lat: float) -> float:
    return _M_PER_DEG_LAT * math.cos(math.radians(lat))


def _polygon_area_m2(coords: list[tuple[float, float]], lat0: float) -> float:
    """Shoelace area for a lon/lat ring, good enough at 5 km scale."""
    if len(coords) < 3:
        return 0.0
    mpd_lon = _mpd_lon(lat0)
    pts = [(lon * mpd_lon, lat * _M_PER_DEG_LAT) for lat, lon in coords]
    acc = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        acc += x1 * y2 - x2 * y1
    return abs(acc) / 2.0


def _line_length_m(coords: list[tuple[float, float]], lat0: float) -> float:
    if len(coords) < 2:
        return 0.0
    mpd_lon = _mpd_lon(lat0)
    total = 0.0
    for (lat1, lon1), (lat2, lon2) in zip(coords, coords[1:]):
        dx = (lon2 - lon1) * mpd_lon
        dy = (lat2 - lat1) * _M_PER_DEG_LAT
        total += math.hypot(dx, dy)
    return total


def overpass_context(min_lat: float, min_lon: float, max_lat: float,
                     max_lon: float, guild_hints: set[str] | None = None) -> dict:
    """Return habitat fractions (0..1) and path density (m per km2)."""
    bbox = "{},{},{},{}".format(min_lat, min_lon, max_lat, max_lon)
    lat0 = (min_lat + max_lat) / 2.0
    box_area = max(1.0, _polygon_area_m2(
        [(min_lat, min_lon), (min_lat, max_lon), (max_lat, max_lon), (max_lat, min_lon)], lat0))

    parts = []
    for name, tags in HABITAT_TAGS.items():
        for tag in tags:
            parts.append('way{}({})'.format(tag, bbox))
    parts.append('way{}({})'.format(PATH_FILTER, bbox))
    query = "[out:json][timeout:60];({});out geom;".format(";".join(parts))

    try:
        r = requests.post(
            OVERPASS,
            data={"data": query},
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT_S + 30,
        )
        r.raise_for_status()
        elements = r.json().get("elements", [])
    except (requests.RequestException, ValueError):
        return {}

    areas = {"forest": 0.0, "heath": 0.0, "wet_nature": 0.0}
    path_len = 0.0
    for el in elements:
        geom = el.get("geometry") or []
        coords = [(p["lat"], p["lon"]) for p in geom if "lat" in p and "lon" in p]
        tags = el.get("tags", {}) or {}
        if tags.get("highway"):
            path_len += _line_length_m(coords, lat0)
            continue
        if tags.get("natural") == "wood" or tags.get("landuse") == "forest":
            areas["forest"] += _polygon_area_m2(coords, lat0)
        elif tags.get("natural") in ("heath", "scrub"):
            areas["heath"] += _polygon_area_m2(coords, lat0)
        elif tags.get("natural") in ("wetland", "marsh"):
            areas["wet_nature"] += _polygon_area_m2(coords, lat0)

    out = {key: min(1.0, val / box_area) for key, val in areas.items()}
    out["path_density"] = path_len / (box_area / 1_000_000.0)  # m per km2
    return out


def protected_flag(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> bool:
    """True when the cell intersects a Natura 2000 area."""
    bbox = "{},{},{},{}".format(min_lon, min_lat, max_lon, max_lat)
    try:
        r = requests.get(
            NATURA2000_ITEMS,
            params={"bbox": bbox, "limit": 1, "f": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT_S,
        )
        if r.status_code >= 400:
            return False
        return bool(r.json().get("features"))
    except (requests.RequestException, ValueError):
        return False


def enrich_cell(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> dict:
    ctx = overpass_context(min_lat, min_lon, max_lat, max_lon)
    ctx["protected"] = protected_flag(min_lat, min_lon, max_lat, max_lon)
    # tree_cover is not derivable from OSM reliably; use forest as a stand-in
    # and let the scoring layer treat it as a coarse proxy.
    ctx["tree_cover"] = ctx.get("forest", 0.0)
    ctx["canopy_height"] = None
    ctx["microrelief"] = None
    ctx["groundwater_depth_cm"] = None
    ctx["soil_type"] = None
    ctx["soil_lime"] = None
    return ctx
