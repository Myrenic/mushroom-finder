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
import threading
import time
import sys
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
    RateLimiter,
    TTLCache,
    binomial,
    query_variants,
    seasonal_score,
    week_of,
    weekly_profile,
    window,
)
from config import ATTRIBUTIONS, GUILDS, MODEL_VERSION  # noqa: E402

API_PORT = int(os.environ.get("API_PORT", "3001"))

# Minimum share of a species' peak week before it is worth listing.
MIN_SEASONAL = 0.10

# De pagina is publiek en alleen-lezen. Deze remmen beschermen de pod en de
# externe diensten (Nominatim staat max 1 verzoek per seconde toe).
API_LIMIT = RateLimiter(limit=180, window_s=60)
SEARCH_LIMIT = RateLimiter(limit=20, window_s=60)
EXPECT_CACHE = TTLCache(ttl_s=900, max_items=2000)


# ── HTTP helpers ──────────────────────────────────────────────────────────
def connect():
    return psycopg.connect(row_factory=dict_row, autocommit=False)


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
        # A floor, because a single stray record in an off week gives a tiny
        # non-zero seasonal score and would otherwise list a species as
        # "in season" in midwinter.
        if seasonal < MIN_SEASONAL:
            continue
        confidence = min(1.0, math.log1p(row["n"]) / math.log1p(50.0))
        expected = seasonal * (0.4 + 0.6 * confidence)
        if seasonal >= 0.7:
            phase = "op z'n best"
        elif seasonal >= 0.35:
            phase = "in seizoen"
        elif seasonal >= 0.10:
            phase = "komt op of loopt af"
        else:
            phase = "buiten seizoen"
        last_seen = row["last_seen"]
        days_ago = (dt.date.today() - last_seen).days if last_seen else None
        reasons = [
            "{} ({}% van z'n piekweek)".format(phase, round(seasonal * 100)),
            "{} waarnemingen binnen {} km".format(row["n"], radius_m // 1000),
        ]
        if days_ago is not None:
            reasons.append(
                "laatst gemeld {}".format(
                    "vandaag" if days_ago <= 1 else "{} dagen geleden".format(days_ago)
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


def meta(conn) -> dict:
    """Bronnen, versies en versheid. Geen run-historie meer: er zijn geen
    knoppen meer die iets starten."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, provider, title, source_url, licence, attribution, "
                    "last_success_at FROM datasets ORDER BY id")
        datasets = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT layer, version, resolution_m FROM layer_versions "
                    "ORDER BY layer")
        layers = [dict(r) for r in cur.fetchall()]
    return {
        "model_version": MODEL_VERSION,
        "datasets": datasets,
        "layers": layers,
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

    def _client_ip(self) -> str:
        # nginx en Traefik zetten X-Forwarded-For; de eerste is de bezoeker.
        forwarded = self.headers.get("X-Forwarded-For", "")
        return forwarded.split(",")[0].strip() if forwarded else self.client_address[0]

    # -- routing --------------------------------------------------------
    # Alleen lezen. Er is geen endpoint meer dat iets kan starten of wijzigen.
    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        try:
            if path == "/api/health":
                return self._send(200, {"status": "ok"})
            if path == "/api/me":
                return self._send(200, self._identity())

            ip = self._client_ip()
            if path == "/api/search" and not SEARCH_LIMIT.allow(ip):
                return self._send(429, {"error": "te veel zoekopdrachten, probeer het zo weer"})
            if not API_LIMIT.allow(ip):
                return self._send(429, {"error": "te veel verzoeken, probeer het zo weer"})

            conn = connect()
            try:
                if path == "/api/meta":
                    return self._send(200, meta(conn))
                if path == "/api/weather":
                    return self._send(200, latest_condition(conn))
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
                        return self._send(400, {"error": "lat en lon zijn verplicht"})
                    if not (50.0 <= lat <= 54.0 and 3.0 <= lon <= 7.5):
                        return self._send(400, {"error": "punt ligt buiten Nederland"})
                    radius_km = min(25, max(1, int((query.get("radius_km") or ["5"])[0])))
                    raw_date = (query.get("date") or [""])[0]
                    target = None
                    if raw_date:
                        try:
                            target = dt.date.fromisoformat(raw_date)
                        except ValueError:
                            return self._send(400, {"error": "datum moet JJJJ-MM-DD zijn"})
                        if not (dt.date(2005, 1, 1) <=
                                target <= dt.date.today() + dt.timedelta(days=400)):
                            return self._send(400, {"error": "datum valt buiten bereik"})
                    # Zelfde plek, straal en datum geeft hetzelfde antwoord;
                    # de query zelf is te duur om onbeperkt te draaien.
                    key = "{}|{}|{}|{}".format(round(lat, 3), round(lon, 3), radius_km,
                                               target.isoformat() if target else "nu")
                    payload = EXPECT_CACHE.get(key)
                    if payload is None:
                        payload = expect_here(conn, lat, lon, radius_km * 1000,
                                              latest_condition(conn), target_date=target)
                        EXPECT_CACHE.set(key, payload)
                    return self._send(200, payload)
                if path == "/api/search":
                    q = (query.get("q") or [""])[0]
                    return self._send(200, {"results": search_places(q)})
            finally:
                conn.close()
            return self._send(404, {"error": "niet gevonden"})
        except Exception as exc:  # noqa: BLE001
            return self._send(500, {"error": str(exc)[:500]})


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
