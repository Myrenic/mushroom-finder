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
import os
import ssl
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
            "activeDeadlineSeconds": 3600 if kind in ("historical", "aoi") else 900,
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
    return [{
        "id": r["id"], "guild": r["guild"], "guild_label": GUILDS.get(r["guild"], r["guild"]),
        "fsp": round(float(r["fsp"]), 3),
        "bucket": scoring.percentile_bucket(float(r["fsp"])),
        "confidence": r["confidence"], "components": r["components"],
        "lat": r["lat"], "lon": r["lon"],
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
