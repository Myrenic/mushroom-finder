"""On-demand 10 m analysis of a selected area.

This is where the fine resolution actually comes from: AHN lidar at 0.5 m
aggregated to 10 m cells, plus locally mapped habitat. The output is a ranked
shortlist of 10 m *search targets* for a guild, never an observation claim.

Requires rasterio + numpy (installed only for this job kind).
"""

from __future__ import annotations

import io
from typing import Callable

import requests

from config import FINE_CELL_SIZE_M, HTTP_TIMEOUT_S, MODEL_VERSION, USER_AGENT
import scoring
import sources_environment

Progress = Callable[[float, str, str], None]

AHN_WCS = "https://service.pdok.nl/rws/ahn/wcs/v1_0"

# Keep an AOI small enough that a single job is seconds, not minutes.
MAX_AOI_SIDE_M = 2000
# A walkable shortlist, not a swarm of dots: keep the best cells that are at
# least MIN_SPACING_M apart so each one is a distinct place to inspect.
MAX_CANDIDATES = 24
MIN_SPACING_M = 150.0


def _wcs_tiff(coverage: str, xmin: float, ymin: float, xmax: float, ymax: float) -> bytes:
    params = {
        "service": "WCS",
        "version": "2.0.1",
        "request": "GetCoverage",
        "coverageId": coverage,
        "format": "image/tiff",
        "subset": ["x({},{})".format(xmin, xmax), "y({},{})".format(ymin, ymax)],
    }
    r = requests.get(AHN_WCS, params=params,
                     headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT_S + 60)
    r.raise_for_status()
    return r.content


def _block_reduce(arr, factor: int, fn):
    import numpy as np

    h, w = arr.shape
    h2, w2 = h // factor * factor, w // factor * factor
    trimmed = arr[:h2, :w2]
    blocks = trimmed.reshape(h2 // factor, factor, w2 // factor, factor)
    with np.errstate(all="ignore"):
        return fn(blocks, axis=(1, 3))


def _load_params(conn, run_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT params FROM refresh_runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
    return (row or {}).get("params") or {} if row else {}


def _wgs_bbox(conn, xmin: float, ymin: float, xmax: float, ymax: float):
    """Authoritative RD->WGS84 bbox via PostGIS (no projection code in Python)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ST_YMin(g) AS min_lat, ST_XMin(g) AS min_lon,
                   ST_YMax(g) AS max_lat, ST_XMax(g) AS max_lon
            FROM (SELECT ST_Transform(
                    ST_MakeEnvelope(%s, %s, %s, %s, 28992), 4326) AS g) s
            """,
            (xmin, ymin, xmax, ymax),
        )
        row = cur.fetchone()
    return row["min_lat"], row["min_lon"], row["max_lat"], row["max_lon"]


def run(conn, run_id: str, progress: Progress, requested_by: str | None = None,
        params: dict | None = None) -> dict:
    import numpy as np
    import rasterio

    params = params or _load_params(conn, run_id)
    guild = params.get("guild") or "mycorrhizal"
    bbox = params.get("bbox_rd") or {}
    xmin = float(bbox.get("xmin"))
    ymin = float(bbox.get("ymin"))
    xmax = float(bbox.get("xmax"))
    ymax = float(bbox.get("ymax"))

    if xmax - xmin > MAX_AOI_SIDE_M or ymax - ymin > MAX_AOI_SIDE_M:
        raise ValueError("AOI too large; max side is {} m".format(MAX_AOI_SIDE_M))

    progress(0.10, "ahn", "fetching AHN DTM/DSM")
    dtm_bytes = _wcs_tiff("dtm_05m", xmin, ymin, xmax, ymax)
    dsm_bytes = _wcs_tiff("dsm_05m", xmin, ymin, xmax, ymax)

    with rasterio.open(io.BytesIO(dtm_bytes)) as src:
        dtm = src.read(1).astype("float64")
        res = abs(src.transform.a)
        dtm_nodata = src.nodata
    with rasterio.open(io.BytesIO(dsm_bytes)) as src:
        dsm = src.read(1).astype("float64")
        dsm_nodata = src.nodata

    if dtm.shape != dsm.shape:
        raise RuntimeError("AHN DTM/DSM shape mismatch")

    # Mask nodata and physically implausible values so a single sentinel does
    # not blow up the standard deviation.
    for arr, nodata in ((dtm, dtm_nodata), (dsm, dsm_nodata)):
        if nodata is not None:
            arr[arr == nodata] = np.nan
    dtm[(dtm < -50.0) | (dtm > 500.0)] = np.nan
    dsm[(dsm < -50.0) | (dsm > 600.0)] = np.nan
    if np.isnan(dtm).all() or np.isnan(dsm).all():
        raise RuntimeError("AHN returned no valid elevation data for this area")
    # Fill remaining gaps with the local mean so block stats stay stable.
    dtm = np.where(np.isnan(dtm), np.nanmean(dtm), dtm)
    dsm = np.where(np.isnan(dsm), np.nanmean(dsm), dsm)

    factor = max(1, int(round(FINE_CELL_SIZE_M / res)))
    progress(0.35, "aggregate", "aggregating {}x to {} m".format(factor, FINE_CELL_SIZE_M))

    elev_mean = _block_reduce(dtm, factor, lambda b, axis: np.mean(b, axis=axis))
    elev_std = _block_reduce(dtm, factor, lambda b, axis: np.std(b, axis=axis))
    canopy = _block_reduce(dsm - dtm, factor, lambda b, axis: np.mean(b, axis=axis))

    elev_mean = np.nan_to_num(elev_mean, nan=0.0)
    elev_std = np.nan_to_num(elev_std, nan=0.0)
    canopy = np.nan_to_num(canopy, nan=0.0)

    # Relative elevation: how low the cell sits versus the AOI median.
    median_elev = float(np.median(elev_mean)) if elev_mean.size else 0.0

    progress(0.55, "habitat", "reading local OSM habitat")
    min_lat, min_lon, max_lat, max_lon = _wgs_bbox(conn, xmin, ymin, xmax, ymax)
    osm = sources_environment.overpass_context(min_lat, min_lon, max_lat, max_lon)

    ny, nx = elev_mean.shape
    lat0 = (min_lat + max_lat) / 2.0
    candidates = []
    for iy in range(ny):
        for ix in range(nx):
            if canopy[iy, ix] <= 0.5 and elev_std[iy, ix] <= 0.02:
                # no structure and no microrelief: nothing to prioritise
                continue
            x_rd = xmin + (ix + 0.5) * FINE_CELL_SIZE_M
            y_rd = ymin + (iy + 0.5) * FINE_CELL_SIZE_M
            std = float(min(0.5, max(0.0, elev_std[iy, ix])))
            can = float(min(40.0, max(0.0, canopy[iy, ix])))
            env = {
                "forest_fraction": osm.get("forest", 0.0),
                "heath_fraction": osm.get("heath", 0.0),
                "wet_nature_fraction": osm.get("wet_nature", 0.0),
                "tree_cover": min(1.0, can / 25.0),
                # shallow hollows retain moisture; deeper groundwater unknown here
                "groundwater_depth_cm": max(25.0, 250.0 - std * 4000.0),
                "microrelief": min(1.0, std * 10.0),
                "path_density": osm.get("path_density", 0.0),
                "protected": osm.get("protected", False),
            }
            components = {
                "habitat": float(scoring.habitat_component(env, guild)),
                "moisture": float(scoring.moisture_component(env)),
                "access": float(scoring.access_component(env)),
                "structure": min(1.0, can / 30.0),
                "hollow": 1.0 - min(1.0, max(0.0, float(elev_mean[iy, ix]) - median_elev) / 5.0),
            }
            fsp = (
                0.40 * components["habitat"]
                + 0.20 * components["moisture"]
                + 0.20 * components["structure"]
                + 0.10 * components["hollow"]
                + 0.10 * components["access"]
            )
            candidates.append({
                "x_rd": x_rd,
                "y_rd": y_rd,
                "fsp": float(fsp),
                "components": components,
            })

    progress(0.80, "rank", "ranking {} cells".format(len(candidates)))
    if not candidates:
        return {"candidates": 0, "note": "no structured 10 m cells in AOI"}

    candidates.sort(key=lambda c: c["fsp"], reverse=True)
    n = len(candidates)
    for rank, c in enumerate(candidates):
        c["rank"] = rank

    top: list[dict] = []
    for c in candidates:
        if all(
            (c["x_rd"] - t["x_rd"]) ** 2 + (c["y_rd"] - t["y_rd"]) ** 2
            >= MIN_SPACING_M ** 2
            for t in top
        ):
            top.append(c)
            if len(top) >= MAX_CANDIDATES:
                break
    # Percentile reflects standing among all scanned cells, not just the ones
    # kept after spacing.
    for c in top:
        c["percentile"] = 1.0 - (c["rank"] / max(1, n))

    # Replace this AOI's previous candidates only (other AOIs stay intact).
    import json
    import uuid

    aoi_key = _aoi_key(xmin, ymin, xmax, ymax, guild)
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM candidate_sites WHERE components->>'aoi_key' = %s",
            (aoi_key,),
        )
        for c in top:
            cur.execute(
                """
                INSERT INTO candidate_sites (id, guild, geom, geom_rd, fsp, confidence, components)
                VALUES (%s, %s,
                        ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 28992), 4326),
                        ST_SetSRID(ST_MakePoint(%s, %s), 28992),
                        %s, 'B', %s)
                """,
                (uuid.uuid4().hex[:12], guild, c["x_rd"], c["y_rd"],
                 c["x_rd"], c["y_rd"], c["fsp"],
                 json.dumps({**c["components"], "model_version": MODEL_VERSION,
                             "aoi": True, "aoi_key": aoi_key,
                             # rank within this area, so "top 10%" is local
                             "percentile": c["percentile"]})),
            )
    conn.commit()

    record_aoi(conn, aoi_key, xmin, ymin, xmax, ymax, guild, len(top))
    progress(1.0, "done", "{} candidates".format(len(top)))
    return {"candidates": len(top), "scanned": n}


def _aoi_key(xmin: float, ymin: float, xmax: float, ymax: float, guild: str) -> str:
    import hashlib

    return hashlib.sha1(
        "{:.0f},{:.0f},{:.0f},{:.0f},{}".format(xmin, ymin, xmax, ymax, guild).encode()
    ).hexdigest()[:16]


def record_aoi(conn, key: str, xmin: float, ymin: float, xmax: float, ymax: float,
               guild: str, count: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO aoi_cache (id, bbox_rd, guild, layer_version, model_version, storage_path)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET last_used_at = now()
            """,
            (key, [xmin, ymin, xmax, ymax], guild, "ahn-mvp", MODEL_VERSION,
             "db:candidate_sites:{}".format(count)),
        )
    conn.commit()



