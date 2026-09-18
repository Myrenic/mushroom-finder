#!/usr/bin/env python3
"""Mushroom Finder API.

Runs as an in-pod sidecar behind nginx. It:

  * serves read-only hotspot / candidate / weather / freshness data;
  * triggers refresh Jobs through the Kubernetes API using a narrowly-scoped
    ServiceAccount (batch/jobs in its own namespace only);
  * never lets the browser supply a Job image, command, env or manifest.

Identity comes from oauth2-proxy via Traefik (``X-Auth-Request-*``). The web
pod is only reachable from the Traefik namespace (NetworkPolicy), so those
headers cannot be spoofed by other in-cluster workloads.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import ssl
import threading
import time
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, "/deps")

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

sys.path.insert(0, "/worker")
import db  # noqa: E402
import scoring  # noqa: E402
from analytics import (  # noqa: E402
    binomial,
    query_variants,
    seasonal_score,
    week_of,
    weekly_profile,
    window,
)
from config import ATTRIBUTIONS, GUILDS, MODEL_VERSION  # noqa: E402

API_PORT = int(os.environ.get("API_PORT", "3001"))
NAMESPACE = os.environ.get("KUBE_NAMESPACE", "services")
WORKER_IMAGE = os.environ.get("WORKER_IMAGE", "docker.io/library/python:3.12-slim")
# rasterio's bundled GDAL needs libexpat.so.1, which the -slim image does not
# ship. The full Debian-based image does, so raster jobs use it.
WORKER_IMAGE_RASTER = os.environ.get("WORKER_IMAGE_RASTER", "docker.io/library/python:3.12")
ADMIN_GROUPS = {g.strip() for g in os.environ.get("ADMIN_GROUPS", "").split(",") if g.strip()}

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
KUBE_HOST = "https://kubernetes.default.svc"

ALL_KINDS = {"weather", "historical", "maintenance", "aoi"}

MAX_AOI_SIDE_M = 2000
NL_RD_LIMITS = {"xmin": -10000.0, "ymin": 280000.0, "xmax": 300000.0, "ymax": 650000.0}


# ── HTTP helpers ──────────────────────────────────────────────────────────
def connect():
    return psycopg.connect(row_factory=dict_row, autocommit=False)


def _kube_token() -> str:
    try:
        with open(os.path.join(SA_DIR, "token"), "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _kube_context() -> ssl.SSLContext:
    ca = os.path.join(SA_DIR, "ca.crt")
    if not os.path.exists(ca):
        raise RuntimeError("service account CA unavailable (not running in-cluster)")
    return ssl.create_default_context(cafile=ca)


def kube_request(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url = KUBE_HOST + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + _kube_token())
    req.add_header("Content-Type", "application/json")
    ctx = _kube_context()
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() or "{}"
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"error": raw[:500]}


def job_name(kind: str, run_id: str) -> str:
    return "mushroom-{}-{}".format(kind, run_id)


def build_job(kind: str, run_id: str) -> dict:
    """Server-generated Job. The browser never influences this shape."""
    name = job_name(kind, run_id)
    image = WORKER_IMAGE_RASTER if kind == "aoi" else WORKER_IMAGE
    labels = {"app.kubernetes.io/name": "mushroom-finder",
              "app.kubernetes.io/component": "worker"}
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": NAMESPACE, "labels": labels},
        "spec": {
            "backoffLimit": 1,
            # Historical imports many taxa over many years; raster AOIs are
            # bounded and quick; weather is a few API calls.
            "activeDeadlineSeconds": {"historical": 7200, "aoi": 1800}.get(kind, 900),
            "ttlSecondsAfterFinished": 600,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "serviceAccountName": "mushroom-finder-worker",
                    "automountServiceAccountToken": False,
                    "restartPolicy": "Never",
                    "securityContext": {"fsGroup": 1000,
                                        "seccompProfile": {"type": "RuntimeDefault"}},
                    "initContainers": [{
                        "name": "deps",
                        "image": image,
                        "command": ["/bin/sh", "-c",
                                    "pip install --no-cache-dir --target=/deps "
                                    "'psycopg[binary]' requests "
                                    + ("rasterio numpy " if kind == "aoi" else "")],
                        "volumeMounts": [{"name": "deps", "mountPath": "/deps"}],
                        "resources": {"requests": {"cpu": "50m", "memory": "128Mi"},
                                      "limits": {"memory": "1Gi"}},
                        "securityContext": {"runAsNonRoot": True, "runAsUser": 1000,
                                            "allowPrivilegeEscalation": False,
                                            "capabilities": {"drop": ["ALL"]}},
                    }],
                    "containers": [{
                        "name": "worker",
                        "image": image,
                        "command": ["python3", "/app/run_refresh.py",
                                    "--kind=" + kind, "--run=" + run_id],
                        "env": [
                            {"name": "PYTHONPATH", "value": "/deps"},
                            {"name": "PGHOST", "value": "mushroom-finder-postgres"},
                            {"name": "PGDATABASE", "value": "mushroom"},
                            {"name": "PGUSER", "value": "mushroom"},
                            {"name": "PGPASSWORD", "valueFrom": {
                                "secretKeyRef": {"name": "mushroom-finder-db",
                                                 "key": "password"}}},
                        ],
                        "volumeMounts": [
                            {"name": "worker-code", "mountPath": "/app", "readOnly": True},
                            {"name": "migrations", "mountPath": "/migrations", "readOnly": True},
                            {"name": "deps", "mountPath": "/deps"},
                        ],
                        "resources": {"requests": {"cpu": "100m", "memory": "256Mi"},
                                      "limits": {"memory": "3Gi"}},
                        "securityContext": {"runAsNonRoot": True, "runAsUser": 1000,
                                            "allowPrivilegeEscalation": False,
                                            "capabilities": {"drop": ["ALL"]}},
                    }],
                    "volumes": [
                        {"name": "worker-code",
                         "configMap": {"name": "mushroom-worker"}},
                        {"name": "migrations",
                         "configMap": {"name": "mushroom-migrations"}},
                        {"name": "deps", "emptyDir": {}},
                    ],
                },
            },
        },
    }


# ── Query helpers ─────────────────────────────────────────────────────────
def latest_condition(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM weather_snapshots WHERE scope='nl' "
                    "ORDER BY as_of DESC LIMIT 1")
        snap = cur.fetchone()
    if not snap:
        return {"condition": 0.5, "as_of": None}
    return {
        "condition": scoring.condition_component(snap),
        "as_of": snap["as_of"].isoformat() if snap["as_of"] else None,
        "precip_7d": snap["precip_7d"], "precip_14d": snap["precip_14d"],
        "precip_21d": snap["precip_21d"], "dry_days": snap["dry_days"],
        "temp_c": snap["temp_c"], "soil_temp_c": snap["soil_temp_c"],
        "soil_moisture": snap["soil_moisture"],
        "forecast_precip_3d": snap["forecast_precip_3d"],
    }


def hotspots(conn, guild: str | None, limit: int) -> list[dict]:
    today = dt.date.today()
    cond = latest_condition(conn)["condition"]
    sql = """
        SELECT cs.cell_id, cs.guild, cs.static_score, cs.confidence, cs.richness,
               cs.last_seen, cs.components, gc.center_lat, gc.center_lon
        FROM cell_scores cs
        JOIN grid_cells gc ON gc.cell_id = cs.cell_id
        {where}
        ORDER BY cs.static_score DESC
        LIMIT %s
    """.format(where="WHERE cs.guild = %s" if guild else "")
    params = ([guild] if guild else []) + [limit]
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    out = []
    for row in rows:
        season = scoring.season_factor(row["guild"], today)
        final = scoring.final_score(row["static_score"], cond, season, row["confidence"])
        out.append({
            "cell_id": row["cell_id"],
            "guild": row["guild"],
            "guild_label": GUILDS.get(row["guild"], row["guild"]),
            "lat": row["center_lat"],
            "lon": row["center_lon"],
            "static_score": round(float(row["static_score"] or 0), 3),
            "final_score": round(float(final), 3),
            "condition_factor": round(float(cond), 3),
            "season_factor": round(float(season), 3),
            "confidence": row["confidence"],
            "richness": row["richness"],
            "last_seen": row["last_seen"].isoformat() if row["last_seen"] else None,
            "components": row["components"],
        })
    out.sort(key=lambda r: r["final_score"], reverse=True)
    return out


def top_species(conn, cell_id: str, limit: int = 6) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.name_nl, s.scientific_name, s.guild, s.photo_value,
                   count(*) AS n, max(o.observed_on) AS last_seen
            FROM occurrences o
            JOIN species s ON s.id = o.species_id
            WHERE o.cell_id = %s AND s.enabled
            GROUP BY 1, 2, 3, 4
            ORDER BY n DESC
            LIMIT %s
            """,
            (cell_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def candidates(conn, guild: str | None, limit: int) -> list[dict]:
    where = "WHERE components->>'aoi' = 'true'"
    params: list = []
    if guild:
        where += " AND guild = %s"
        params.append(guild)
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, guild, fsp, confidence, components,
                   ST_Y(geom) AS lat, ST_X(geom) AS lon
            FROM candidate_sites
            {where}
            ORDER BY fsp DESC LIMIT %s
            """.format(where=where),
            params,
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        components = r["components"] or {}
        # Rank within the analysed area is the meaningful bucket; an area whose
        # best cell is modest should not label all of them "lower priority".
        pct = components.get("percentile")
        rank = float(pct) if isinstance(pct, (int, float)) else float(r["fsp"])
        out.append({
            "id": r["id"], "guild": r["guild"],
            "guild_label": GUILDS.get(r["guild"], r["guild"]),
            "fsp": round(float(r["fsp"]), 3),
            "bucket": scoring.percentile_bucket(rank),
            "confidence": r["confidence"], "components": components,
            "lat": r["lat"], "lon": r["lon"],
        })
    return out


# ── Place search ─────────────────────────────────────────────────────────
# Two providers, because neither is complete: PDOK (Dutch government) is best
# for towns and addresses, Nominatim/OSM is best for nature areas and water.
# Dutch diminutives ("hemelriekje") are not indexed anywhere, so the query is
# progressively relaxed before giving up.
_GEO_CACHE: dict[str, list] = {}
_GEO_LOCK = threading.Lock()
_GEO_LAST = [0.0]
_GEO_CACHE_MAX = 300

PDOK_FREE = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
POINT_RE = re.compile(r"POINT\(\s*([-\d.]+)\s+([-\d.]+)\s*\)")


def _http_json(url: str, params: dict, timeout: int = 20):
    import urllib.parse

    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={
        "User-Agent": "mushroom-finder/0.1 (private household tool)",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001 - a dead provider must not break search
        return None


def _search_pdok(q: str, limit: int) -> list[dict]:
    data = _http_json(PDOK_FREE, {
        "q": q, "rows": limit,
        "fl": "id,weergavenaam,type,centroide_ll,gemeentenaam,provincienaam",
    })
    out = []
    for doc in ((data or {}).get("response", {}) or {}).get("docs", []):
        m = POINT_RE.search(doc.get("centroide_ll") or "")
        if not m:
            continue
        out.append({
            "id": "pdok:" + str(doc.get("id")),
            "name": doc.get("weergavenaam") or q,
            "type": doc.get("type") or "place",
            "lat": float(m.group(2)),
            "lon": float(m.group(1)),
            "municipality": doc.get("gemeentenaam"),
            "province": doc.get("provincienaam"),
            "source": "PDOK",
        })
    return out


def _search_nominatim(q: str, limit: int) -> list[dict]:
    # Nominatim's usage policy allows max one request per second.
    with _GEO_LOCK:
        wait = 1.1 - (time.time() - _GEO_LAST[0])
        if wait > 0:
            time.sleep(wait)
        _GEO_LAST[0] = time.time()
    data = _http_json(NOMINATIM, {
        "q": q, "format": "json", "limit": limit, "addressdetails": 1,
    })
    out = []
    for doc in data or []:
        try:
            lat, lon = float(doc["lat"]), float(doc["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        addr = doc.get("address") or {}
        out.append({
            "id": "osm:" + str(doc.get("osm_id") or doc.get("place_id")),
            "name": doc.get("display_name") or q,
            "type": doc.get("type") or "place",
            "lat": lat,
            "lon": lon,
            "municipality": addr.get("municipality") or addr.get("town") or addr.get("village"),
            "province": addr.get("state"),
            "source": "OpenStreetMap",
        })
    return out


# When searching "veluwe" the government geocoder happily returns streets
# named Veluwe before the actual Veluwe area. Prefer area-like results.
_AREA_TYPES = {
    "protected_area", "nature_reserve", "forest", "water", "wetland", "heath",
    "park", "moor", "recreation_ground", "administrative", "municipality",
    "gemeente", "provincie", "woonplaats", "city", "town", "village", "hamlet",
}
_STREET_TYPES = {
    "weg", "adres", "perceel", "postcode", "pad", "straat", "road", "path",
    "footway", "house", "railway",
}


def _type_rank(place: dict) -> int:
    t = (place.get("type") or "").lower()
    if t in _AREA_TYPES:
        return 0
    if t in _STREET_TYPES:
        return 2
    return 1


def search_places(q: str, limit: int = 6) -> list[dict]:
    q = (q or "").strip()
    if len(q) < 3:
        return []
    key = q.lower()
    with _GEO_LOCK:
        if key in _GEO_CACHE:
            return _GEO_CACHE[key]

    variants = query_variants(q)

    results: list[dict] = []
    seen = set()

    def add(items):
        for it in items:
            k = (round(it["lat"], 3), round(it["lon"], 3))
            if k in seen:
                continue
            seen.add(k)
            results.append(it)

    for variant in variants:
        add(_search_pdok(variant, limit))
        add(_search_nominatim(variant, limit))
        # Stop at the first query form that produces anything, so a relaxed
        # variant is only tried when the literal one finds nothing.
        if results:
            break

    results.sort(key=_type_rank)  # stable: keep relevance within each band
    results = results[:limit]
    if results:
        with _GEO_LOCK:
            if len(_GEO_CACHE) >= _GEO_CACHE_MAX:
                _GEO_CACHE.clear()
            _GEO_CACHE[key] = results
    return results


SQL_EXPECT = """
WITH me AS (
    SELECT ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography AS g
),
local AS (
    SELECT s.id, s.name_nl, s.scientific_name, s.guild, s.photo_value,
           s.image_url, s.image_credit, s.image_credit_url,
           count(*) AS n, max(o.observed_on) AS last_seen
    FROM occurrences o
    JOIN species s ON s.id = o.species_id
    CROSS JOIN me
    WHERE s.enabled AND NOT s.sensitive AND o.geom IS NOT NULL
      AND ST_DWithin(o.geom::geography, me.g, %(radius_m)s)
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
),
weeks AS (
    SELECT s.id AS sid, date_part('week', o.observed_on)::int AS wk, count(*) AS c
    FROM occurrences o
    JOIN species s ON s.id = o.species_id
    WHERE s.enabled AND NOT s.sensitive AND o.observed_on IS NOT NULL
    GROUP BY 1, 2
),
prof AS (
    SELECT sid, array_agg(wk ORDER BY wk) AS wks, array_agg(c ORDER BY wk) AS cnts
    FROM weeks GROUP BY sid
)
SELECT l.id, l.name_nl, l.scientific_name, l.guild, l.photo_value,
       l.image_url, l.image_credit, l.image_credit_url,
       l.n, l.last_seen, p.wks, p.cnts
FROM local l
LEFT JOIN prof p ON p.sid = l.id
ORDER BY l.n DESC
LIMIT 60
"""


def _period_actuals(conn, lat: float, lon: float, radius_m: int,
                    target: dt.date) -> dict:
    """What was actually reported near a point around that date, that year."""
    lo = target - dt.timedelta(days=30)
    hi = target + dt.timedelta(days=30)
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH me AS (
                SELECT ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography AS g
            )
            SELECT o.species_id, count(*) AS n, max(o.observed_on) AS last_seen
            FROM occurrences o
            CROSS JOIN me
            WHERE o.geom IS NOT NULL
              AND o.observed_on BETWEEN %(lo)s AND %(hi)s
              AND ST_DWithin(o.geom::geography, me.g, %(radius_m)s)
            GROUP BY 1
            """,
            {"lon": lon, "lat": lat, "radius_m": radius_m, "lo": lo, "hi": hi},
        )
        return {r["species_id"]: dict(r) for r in cur.fetchall()}


def expect_here(conn, lat: float, lon: float, radius_m: int,
                weather: dict | None = None,
                target_date: dt.date | None = None) -> dict:
    """Rank the species known in this area by how likely they are right now.

    Ranking uses local records and the species' own national phenology (how
    close today is to its peak). Weather is deliberately NOT in the ranking:
    a national test found only a ~10% effect that does not survive per-guild
    scrutiny, so it is reported as context for the reader to judge.
    """
    today = dt.date.today()
    target = target_date or today
    with conn.cursor() as cur:
        cur.execute(SQL_EXPECT, {"lat": lat, "lon": lon, "radius_m": radius_m})
        rows = cur.fetchall()

    # For a past date we can also show what was really reported then.
    actuals = {} if target > today else _period_actuals(conn, lat, lon, radius_m, target)

    week = week_of(target)
    month = target.month
    out = []
    for row in rows:
        arr = weekly_profile(row["wks"], row["cnts"])
        if not any(arr):
            continue
        seasonal = seasonal_score(arr, week)
        if seasonal <= 0:
            continue
        confidence = min(1.0, math.log1p(row["n"]) / math.log1p(50.0))
        expected = seasonal * (0.4 + 0.6 * confidence)
        if seasonal >= 0.7:
            phase = "at peak"
        elif seasonal >= 0.35:
            phase = "in season"
        elif seasonal >= 0.10:
            phase = "starting / ending"
        else:
            phase = "out of season"
        last_seen = row["last_seen"]
        days_ago = (dt.date.today() - last_seen).days if last_seen else None
        reasons = [
            "{} ({}% of its peak week)".format(phase, round(seasonal * 100)),
            "{} records within {} km".format(row["n"], radius_m // 1000),
        ]
        if days_ago is not None:
            reasons.append(
                "last reported here {}".format(
                    "today" if days_ago <= 1 else "{} days ago".format(days_ago)
                )
            )
        act = actuals.get(row["id"]) or {}
        out.append({
            "species_id": row["id"],
            "name_nl": row["name_nl"],
            "scientific_name": binomial(row["scientific_name"]),
            "guild": row["guild"],
            "guild_label": GUILDS.get(row["guild"], row["guild"]),
            "photo_value": row["photo_value"],
            "image_url": row["image_url"],
            "image_credit": row["image_credit"],
            "image_credit_url": row["image_credit_url"],
            # Records around the selected date in the selected year (0 in future)
            "period_records": act.get("n", 0),
            "period_last_seen": (act["last_seen"].isoformat()
                                 if act.get("last_seen") else None),
            "local_records": row["n"],
            "last_seen": last_seen.isoformat() if last_seen else None,
            "days_ago": days_ago,
            "seasonal": round(seasonal, 3),
            "peak_week": max(range(1, 54), key=lambda w: window(arr, w)),
            "expected": round(expected, 3),
            "confidence": round(confidence, 2),
            "phase": phase,
            "reasons": reasons,
        })
    out.sort(key=lambda r: r["expected"], reverse=True)
    return {
        "lat": lat,
        "lon": lon,
        "radius_km": radius_m // 1000,
        "date": target.isoformat(),
        "week": week,
        "month": month,
        "is_future": target > today,
        "known_total": len(rows),
        # Context only; not an input to the ranking above.
        "weather": weather or {},
        "species": out,
    }


def recent_reports(conn, guild: str | None, days: int, limit: int) -> list[dict]:
    """Recent observations at the precision each record actually supports."""
    where = []
    params: list = []
    if guild:
        where.append("guild = %s")
        params.append(guild)
    if days > 0:
        where.append("observed_on >= current_date - %s")
        params.append(days)
    params.append(limit)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, guild, name_nl, scientific_name, observed_on,
                   resolution_m, precise, lat, lon
            FROM recent_reports
            {clause}
            ORDER BY observed_on DESC, name_nl
            LIMIT %s
            """.format(clause=clause),
            params,
        )
        rows = cur.fetchall()
    return [{
        "id": r["id"],
        "guild": r["guild"],
        "guild_label": GUILDS.get(r["guild"], r["guild"]),
        "name_nl": r["name_nl"],
        "scientific_name": binomial(r["scientific_name"]),
        "observed_on": r["observed_on"].isoformat(),
        "days_ago": (dt.date.today() - r["observed_on"]).days,
        "resolution_m": r["resolution_m"],
        "precise": r["precise"],
        "lat": r["lat"],
        "lon": r["lon"],
    } for r in rows]


def fine_cells(conn, guild: str | None, days: int, limit: int) -> list[dict]:
    """1 km cells built from precise records only.

    `days` > 0 filters to cells with a report in that window, which is the
    "someone found one recently" view.
    """
    where = []
    params: list = []
    if guild:
        where.append("guild = %s")
        params.append(guild)
    if days > 0:
        where.append("last_seen >= current_date - %s")
        params.append(days)
    params.append(limit)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cell_id, guild, n, years, first_seen, last_seen, recent_n,
                   center_lat, center_lon
            FROM fine_cells
            {clause}
            ORDER BY (last_seen >= current_date - 30) DESC, last_seen DESC, n DESC
            LIMIT %s
            """.format(clause=clause),
            params,
        )
        rows = cur.fetchall()
    return [{
        "cell_id": r["cell_id"],
        "guild": r["guild"],
        "guild_label": GUILDS.get(r["guild"], r["guild"]),
        "n": r["n"],
        "years": r["years"],
        "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
        "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
        "recent_n": r["recent_n"],
        "days_ago": (dt.date.today() - r["last_seen"]).days if r["last_seen"] else None,
        "lat": r["center_lat"],
        "lon": r["center_lon"],
        "resolution_m": 1000,
    } for r in rows]


def meta(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT id, provider, title, source_url, licence, attribution, "
                    "last_success_at FROM datasets ORDER BY id")
        datasets = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM layer_versions ORDER BY layer")
        layers = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT kind, status, progress, phase, message, started_at, "
                    "finished_at, error, stats FROM refresh_runs "
                    "ORDER BY started_at DESC LIMIT 12")
        runs = []
        for r in cur.fetchall():
            r = dict(r)
            for key in ("started_at", "finished_at"):
                r[key] = r[key].isoformat() if r[key] else None
            runs.append(r)
    return {
        "model_version": MODEL_VERSION,
        "datasets": datasets,
        "layers": layers,
        "runs": runs,
        "attributions": ATTRIBUTIONS,
        "weather": latest_condition(conn),
    }


# ── Request handler ───────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "mushroom-finder/0.1"

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- utilities ------------------------------------------------------
    def _send(self, status: int, body: dict | list) -> None:
        raw = json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _identity(self) -> dict:
        email = self.headers.get("X-Auth-Request-Email", "")
        user = self.headers.get("X-Auth-Request-User", "")
        groups = self.headers.get("X-Auth-Request-Groups", "")
        return {
            "email": email,
            "user": user,
            "groups": [g.strip() for g in groups.split(",") if g.strip()],
            "authenticated": bool(email or user),
        }

    def _is_admin(self, ident: dict) -> bool:
        if not ADMIN_GROUPS:
            return ident["authenticated"]
        return bool(ADMIN_GROUPS.intersection(set(ident["groups"])))

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode() or "{}")
        except ValueError:
            return {}

    # -- routing --------------------------------------------------------
    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        try:
            if path == "/api/health":
                return self._send(200, {"status": "ok"})
            if path == "/api/me":
                return self._send(200, self._identity())
            conn = connect()
            try:
                if path == "/api/meta":
                    return self._send(200, meta(conn))
                if path == "/api/weather":
                    return self._send(200, latest_condition(conn))
                if path == "/api/hotspots":
                    guild = (query.get("guild") or [None])[0]
                    limit = min(2000, int((query.get("limit") or ["800"])[0]))
                    return self._send(200, {"hotspots": hotspots(conn, guild, limit),
                                            "guilds": GUILDS})
                if path.startswith("/api/hotspots/"):
                    cell_id = path.rsplit("/", 1)[1]
                    return self._send(200, {
                        "cell_id": cell_id,
                        "species": top_species(conn, cell_id),
                    })
                if path == "/api/candidates":
                    guild = (query.get("guild") or [None])[0]
                    limit = min(2000, int((query.get("limit") or ["400"])[0]))
                    return self._send(200, {"candidates": candidates(conn, guild, limit)})
                if path == "/api/fine-cells":
                    guild = (query.get("guild") or [None])[0]
                    days = max(0, min(3650, int((query.get("days") or ["0"])[0])))
                    limit = min(5000, int((query.get("limit") or ["2000"])[0]))
                    return self._send(200, {"cells": fine_cells(conn, guild, days, limit)})
                if path == "/api/recent":
                    guild = (query.get("guild") or [None])[0]
                    days = max(1, min(400, int((query.get("days") or ["90"])[0])))
                    limit = min(5000, int((query.get("limit") or ["1500"])[0]))
                    return self._send(200, {"reports": recent_reports(conn, guild, days, limit)})
                if path == "/api/expect":
                    try:
                        lat = float((query.get("lat") or [""])[0])
                        lon = float((query.get("lon") or [""])[0])
                    except ValueError:
                        return self._send(400, {"error": "lat and lon are required"})
                    if not (50.0 <= lat <= 54.0 and 3.0 <= lon <= 7.5):
                        return self._send(400, {"error": "point is outside the Netherlands"})
                    radius_km = min(25, max(1, int((query.get("radius_km") or ["5"])[0])))
                    raw_date = (query.get("date") or [""])[0]
                    target = None
                    if raw_date:
                        try:
                            target = dt.date.fromisoformat(raw_date)
                        except ValueError:
                            return self._send(400, {"error": "date must be YYYY-MM-DD"})
                        if not (dt.date(2005, 1, 1) <=
                                target <= dt.date.today() + dt.timedelta(days=400)):
                            return self._send(400, {"error": "date is out of range"})
                    return self._send(200, expect_here(
                        conn, lat, lon, radius_km * 1000, latest_condition(conn),
                        target_date=target))
                if path == "/api/search":
                    q = (query.get("q") or [""])[0]
                    return self._send(200, {"results": search_places(q)})
                if path == "/api/refresh":
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT id, kind, status, progress, phase, message, "
                            "started_at, finished_at, error, stats FROM refresh_runs "
                            "ORDER BY started_at DESC LIMIT 20")
                        runs = []
                        for r in cur.fetchall():
                            r = dict(r)
                            for key in ("started_at", "finished_at"):
                                r[key] = r[key].isoformat() if r[key] else None
                            runs.append(r)
                    return self._send(200, {"runs": runs})
            finally:
                conn.close()
            return self._send(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            return self._send(500, {"error": str(exc)[:500]})

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/api/refresh":
                return self._trigger(None)
            if path == "/api/aoi":
                return self._trigger("aoi")
            return self._send(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            return self._send(500, {"error": str(exc)[:500]})

    def do_DELETE(self):  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/api/refresh/"):
            run_id = path.rsplit("/", 1)[1]
            conn = connect()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT kind FROM refresh_runs WHERE id=%s", (run_id,))
                    row = cur.fetchone()
                if not row:
                    return self._send(404, {"error": "unknown run"})
                kube_request("DELETE", "/apis/batch/v1/namespaces/{}/jobs/{}".format(
                    NAMESPACE, job_name(row["kind"], run_id)))
                with conn.cursor() as cur:
                    cur.execute("UPDATE refresh_runs SET status='cancelled', "
                                "finished_at=now(), phase='cancelled' WHERE id=%s "
                                "AND status IN ('queued','running')", (run_id,))
                conn.commit()
            finally:
                conn.close()
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    def _trigger(self, forced_kind: str | None) -> None:
        body = self._read_json()
        kind = forced_kind or body.get("kind")
        if kind not in ALL_KINDS:
            return self._send(400, {"error": "unknown kind"})

        ident = self._identity()
        if not ident["authenticated"]:
            return self._send(401, {"error": "not signed in"})
        if kind != "aoi" and not self._is_admin(ident):
            return self._send(403, {"error": "refresh requires an admin group"})

        params: dict = {}
        if kind == "aoi":
            params = _aoi_shape(body)

        conn = connect()
        if kind == "aoi":
            try:
                params = _aoi_to_rd(conn, params)
            except ValueError as exc:
                conn.close()
                return self._send(400, {"error": str(exc)})

        run_id = db.new_run_id()
        try:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO refresh_runs (id, kind, status, requested_by, "
                        "params, phase, message) VALUES (%s, %s, 'queued', %s, %s, "
                        "'queued', 'waiting for worker')",
                        (run_id, kind, ident["email"] or ident["user"],
                         json.dumps(params)),
                    )
                conn.commit()
            except psycopg.errors.UniqueViolation:
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute("SELECT id, status, progress, phase FROM refresh_runs "
                                "WHERE kind=%s AND status IN ('queued','running') "
                                "ORDER BY started_at DESC LIMIT 1", (kind,))
                    existing = cur.fetchone()
                return self._send(409, {"error": "already running", "run": existing})

            try:
                status, resp = kube_request(
                    "POST", "/apis/batch/v1/namespaces/{}/jobs".format(NAMESPACE),
                    build_job(kind, run_id))
            except Exception as exc:  # noqa: BLE001 - always clear the run row
                db.finish_run(conn, run_id, "failed", None,
                              "job create error: {}".format(exc)[:500])
                return self._send(502, {"error": "could not start worker job",
                                        "detail": str(exc)[:300]})
            if status not in (200, 201):
                db.finish_run(conn, run_id, "failed", None,
                              "job create failed: {}".format(resp)[:500])
                return self._send(502, {"error": "could not start worker job",
                                        "detail": str(resp)[:300]})
            return self._send(202, {"run_id": run_id, "kind": kind})
        finally:
            conn.close()


def _aoi_shape(body: dict) -> dict:
    """Validate the raw WGS84 bbox from the browser (shape + guild only)."""
    guild = body.get("guild") or "mycorrhizal"
    if guild not in GUILDS:
        raise ValueError("unknown guild")
    bbox = body.get("bbox_wgs84") or {}
    try:
        south, west = float(bbox["south"]), float(bbox["west"])
        north, east = float(bbox["north"]), float(bbox["east"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("bbox_wgs84 with south/west/north/east is required")
    if not (south < north and west < east):
        raise ValueError("invalid bbox")
    return {"guild": guild,
            "bbox_wgs84": {"south": south, "west": west, "north": north, "east": east}}


def _aoi_to_rd(conn, params: dict) -> dict:
    """Convert to RD in PostGIS and enforce the size/NL limits there."""
    b = params["bbox_wgs84"]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ST_XMin(g) AS xmin, ST_YMin(g) AS ymin,
                   ST_XMax(g) AS xmax, ST_YMax(g) AS ymax
            FROM (SELECT ST_Transform(
                    ST_MakeEnvelope(%s, %s, %s, %s, 4326), 28992) AS g) s
            """,
            (b["west"], b["south"], b["east"], b["north"]),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError("could not project bbox")
    xmin, ymin, xmax, ymax = (float(row["xmin"]), float(row["ymin"]),
                              float(row["xmax"]), float(row["ymax"]))
    if not (xmin < xmax and ymin < ymax):
        raise ValueError("degenerate bbox")
    if xmax - xmin > MAX_AOI_SIDE_M or ymax - ymin > MAX_AOI_SIDE_M:
        raise ValueError("Area too large: max {} m per side".format(MAX_AOI_SIDE_M))
    if not (NL_RD_LIMITS["xmin"] <= xmin and xmax <= NL_RD_LIMITS["xmax"]
            and NL_RD_LIMITS["ymin"] <= ymin and ymax <= NL_RD_LIMITS["ymax"]):
        raise ValueError("Area is outside the Netherlands")
    return {"guild": params["guild"],
            "bbox_rd": {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}}


def main() -> None:
    conn = db.connect_with_retry()
    try:
        applied = db.apply_migrations(conn, "/migrations")
        print("migrations applied:", applied, flush=True)
    finally:
        conn.close()
    server = ThreadingHTTPServer(("0.0.0.0", API_PORT), Handler)
    print("mushroom-finder api listening on", API_PORT, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
