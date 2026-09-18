"""Thin Postgres/PostGIS helpers shared by the API and the worker.

Deliberately dependency-light: psycopg only. Connection parameters come from
the standard PG* environment variables so credentials never appear in code.
"""

from __future__ import annotations

import glob
import os
import uuid

import psycopg
from psycopg.rows import dict_row


def connect():
    return psycopg.connect(row_factory=dict_row, autocommit=False)


def apply_migrations(conn, directory: str = "/migrations") -> list[str]:
    """Apply every *.sql migration in lexical order. All are idempotent."""
    applied = []
    for path in sorted(glob.glob(os.path.join(directory, "*.sql"))):
        with open(path, "r", encoding="utf-8") as fh:
            sql = fh.read()
        with conn.cursor() as cur:
            cur.execute(sql)
        applied.append(os.path.basename(path))
    conn.commit()
    return applied


# ── Refresh runs ──────────────────────────────────────────────────────────
TERMINAL = ("succeeded", "failed", "cancelled")


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def start_run(conn, run_id: str, kind: str, requested_by: str | None) -> None:
    """Worker-side: claim a queued run and mark it running."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO refresh_runs (id, kind, status, requested_by, phase, message)
            VALUES (%s, %s, 'running', %s, 'starting', 'worker started')
            ON CONFLICT (id) DO UPDATE
              SET status = 'running', phase = 'starting', heartbeat_at = now()
            """,
            (run_id, kind, requested_by),
        )
    conn.commit()


def heartbeat(conn, run_id: str, progress: float, phase: str, message: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE refresh_runs
            SET progress = %s, phase = %s, message = %s, heartbeat_at = now()
            WHERE id = %s
            """,
            (max(0.0, min(1.0, progress)), phase, message, run_id),
        )
    conn.commit()


def finish_run(conn, run_id: str, status: str, stats: dict | None = None,
               error: str | None = None) -> None:
    import json

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE refresh_runs
            SET status = %s, finished_at = now(), progress = 1.0,
                phase = %s, stats = %s, error = %s, heartbeat_at = now()
            WHERE id = %s
            """,
            (status, status, json.dumps(stats or {}), error, run_id),
        )
    conn.commit()


# ── Provenance ────────────────────────────────────────────────────────────
def upsert_dataset(conn, dataset: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO datasets (id, provider, title, source_url, licence,
                                  attribution, version)
            VALUES (%(id)s, %(provider)s, %(title)s, %(source_url)s, %(licence)s,
                    %(attribution)s, %(version)s)
            ON CONFLICT (id) DO UPDATE SET
              provider = EXCLUDED.provider,
              title = EXCLUDED.title,
              source_url = EXCLUDED.source_url,
              licence = EXCLUDED.licence,
              attribution = EXCLUDED.attribution,
              version = EXCLUDED.version,
              updated_at = now()
            """,
            dataset,
        )


def mark_dataset_success(conn, dataset_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE datasets SET last_success_at = now() WHERE id = %s",
            (dataset_id,),
        )


def upsert_species(conn, species: dict) -> None:
    import json

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO species (id, taxon_key, scientific_name, name_nl, guild,
                                 phenology, photo_value, sensitive, enabled)
            VALUES (%(id)s, %(taxon_key)s, %(scientific_name)s, %(name_nl)s,
                    %(guild)s, %(phenology)s, %(photo_value)s, %(sensitive)s, true)
            ON CONFLICT (id) DO UPDATE SET
              taxon_key = EXCLUDED.taxon_key,
              scientific_name = EXCLUDED.scientific_name,
              name_nl = EXCLUDED.name_nl,
              guild = EXCLUDED.guild,
              phenology = EXCLUDED.phenology,
              photo_value = EXCLUDED.photo_value,
              sensitive = EXCLUDED.sensitive,
              enabled = true,
              updated_at = now()
            """,
            {**species, "phenology": json.dumps(species.get("phenology", {}))},
        )


def upsert_attributions(conn, rows: list[dict]) -> None:
    with conn.cursor() as cur:
        for i, row in enumerate(rows):
            cur.execute(
                """
                INSERT INTO attributions (id, label, url, licence, required, sort_order)
                VALUES (%(id)s, %(label)s, %(url)s, %(licence)s, true, %(sort)s)
                ON CONFLICT (id) DO UPDATE SET
                  label = EXCLUDED.label, url = EXCLUDED.url,
                  licence = EXCLUDED.licence, sort_order = EXCLUDED.sort_order
                """,
                {**row, "sort": i},
            )


def upsert_cell_environment(conn, cell_id: str, env: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cell_environment (
              cell_id, forest_fraction, broadleaf_fraction, conifer_fraction,
              heath_fraction, wet_nature_fraction, tree_cover, canopy_height,
              microrelief, groundwater_depth_cm, soil_type, soil_lime,
              protected, path_density, updated_at)
            VALUES (%(cell_id)s, %(forest_fraction)s, %(broadleaf_fraction)s,
                    %(conifer_fraction)s, %(heath_fraction)s, %(wet_nature_fraction)s,
                    %(tree_cover)s, %(canopy_height)s, %(microrelief)s,
                    %(groundwater_depth_cm)s, %(soil_type)s, %(soil_lime)s,
                    %(protected)s, %(path_density)s, now())
            ON CONFLICT (cell_id) DO UPDATE SET
              forest_fraction = EXCLUDED.forest_fraction,
              broadleaf_fraction = EXCLUDED.broadleaf_fraction,
              conifer_fraction = EXCLUDED.conifer_fraction,
              heath_fraction = EXCLUDED.heath_fraction,
              wet_nature_fraction = EXCLUDED.wet_nature_fraction,
              tree_cover = EXCLUDED.tree_cover,
              canopy_height = EXCLUDED.canopy_height,
              microrelief = EXCLUDED.microrelief,
              groundwater_depth_cm = EXCLUDED.groundwater_depth_cm,
              soil_type = EXCLUDED.soil_type,
              soil_lime = EXCLUDED.soil_lime,
              protected = EXCLUDED.protected,
              path_density = EXCLUDED.path_density,
              updated_at = now()
            """,
            {"cell_id": cell_id, **env},
        )


def upsert_cell_scores(conn, rows: list[dict]) -> None:
    import json

    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                INSERT INTO cell_scores (
                  cell_id, guild, recurrence, richness, last_seen, effort,
                  habitat, moisture, access, static_score, confidence,
                  components, updated_at)
                VALUES (%(cell_id)s, %(guild)s, %(recurrence)s, %(richness)s,
                        %(last_seen)s, %(effort)s, %(habitat)s, %(moisture)s,
                        %(access)s, %(static_score)s, %(confidence)s,
                        %(components)s, now())
                ON CONFLICT (cell_id, guild) DO UPDATE SET
                  recurrence = EXCLUDED.recurrence,
                  richness = EXCLUDED.richness,
                  last_seen = EXCLUDED.last_seen,
                  effort = EXCLUDED.effort,
                  habitat = EXCLUDED.habitat,
                  moisture = EXCLUDED.moisture,
                  access = EXCLUDED.access,
                  static_score = EXCLUDED.static_score,
                  confidence = EXCLUDED.confidence,
                  components = EXCLUDED.components,
                  updated_at = now()
                """,
                {**row, "components": json.dumps(row.get("components", {}))},
            )


def record_layer_version(conn, layer: str, version: str, resolution_m: int | None,
                         extent: str | None, storage_path: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO layer_versions (layer, version, resolution_m, extent, storage_path)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (layer) DO UPDATE SET
              version = EXCLUDED.version,
              resolution_m = EXCLUDED.resolution_m,
              extent = EXCLUDED.extent,
              storage_path = EXCLUDED.storage_path,
              updated_at = now()
            """,
            (layer, version, resolution_m, extent, storage_path),
        )
    conn.commit()
