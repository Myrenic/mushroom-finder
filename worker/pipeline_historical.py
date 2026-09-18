"""Historical occurrence import + coarse scoring.

Flow:
  1. resolve the curated taxa against the GBIF backbone;
  2. fetch permitted Dutch records per taxon and store them generalised;
  3. derive 5x5 km cells;
  4. enrich the cells that have evidence with open environmental context;
  5. recompute per-cell, per-guild static scores.

Nothing finer than a 5x5 km cell is ever derived from observation records.
"""

from __future__ import annotations

import json
import os
from typing import Callable

from config import ATTRIBUTIONS, TAXA
import db
import grid
import pipeline_score
import sources_environment
import sources_gbif

Progress = Callable[[float, str, str], None]

GBIF_DATASET = {
    "id": "gbif-observation-org",
    "provider": "GBIF / Observation.org",
    "title": "Observation.org, Nature data from around the World",
    "source_url": "https://doi.org/10.15468/5nilie",
    "licence": "CC BY-NC 4.0",
    "attribution": "Observation.org via GBIF",
    "version": "live",
}

MAX_ENRICH_CELLS = int(os.environ.get("MAX_ENRICH_CELLS", "200"))


def _insert_occurrences(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    inserted = 0
    chunk = 400
    with conn.cursor() as cur:
        for start in range(0, len(rows), chunk):
            batch = rows[start:start + chunk]
            values = []
            params = []
            for r in batch:
                values.append("(%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), "
                              "%s, %s, %s, %s)")
                params.extend([
                    r.get("dataset_id"), r.get("species_id"), r.get("observed_on"),
                    r.get("source_id"), r.get("lon"), r.get("lat"),
                    r.get("coord_uncertainty_m"), r.get("generalized"),
                    r.get("licence"), json.dumps(r.get("raw", {})),
                ])
            cur.execute(
                "INSERT INTO occurrences (dataset_id, species_id, observed_on, source_id, "
                "geom, coord_uncertainty_m, generalized, licence, raw) VALUES "
                + ", ".join(values)
                + " ON CONFLICT (dataset_id, source_id) DO NOTHING",
                params,
            )
            inserted += cur.rowcount
    conn.commit()
    return inserted


def run(conn, run_id: str, progress: Progress, requested_by: str | None = None) -> dict:
    progress(0.02, "provenance", "recording dataset")
    db.upsert_dataset(conn, GBIF_DATASET)
    db.upsert_attributions(conn, ATTRIBUTIONS)

    # ── Resolve taxa ─────────────────────────────────────────────────────
    resolved = []
    for i, taxon in enumerate(TAXA):
        match = sources_gbif.match_species(taxon["sci"])
        if not match:
            progress(0.05 + 0.10 * i / len(TAXA), "taxa",
                     "no GBIF match for {}".format(taxon["sci"]))
            continue
        species_id = taxon["sci"].lower().replace(" ", "-")
        db.upsert_species(conn, {
            "id": species_id,
            "taxon_key": match["taxon_key"],
            "scientific_name": match["scientific_name"] or taxon["sci"],
            "name_nl": taxon.get("nl"),
            "guild": taxon["guild"],
            "phenology": {},
            "photo_value": taxon.get("photo", 3),
            "sensitive": bool(taxon.get("sensitive", False)),
        })
        resolved.append((taxon, species_id, match["taxon_key"]))
    conn.commit()

    # ── Fetch occurrences ────────────────────────────────────────────────
    # Sample each taxon one year at a time so recurrence sees the whole record
    # period rather than whichever years sit first in the result set.
    import datetime as dt

    import config as cfg

    buckets = [(y, y) for y in range(cfg.GBIF_YEAR_FROM, dt.date.today().year + 1)]
    per_bucket = max(1, cfg.GBIF_MAX_RECORDS_PER_TAXON // len(buckets))
    total_inserted = 0
    for i, (taxon, species_id, key) in enumerate(resolved):
        def cb(p: float, phase: str, msg: str, _i=i, _name=taxon["sci"]) -> None:
            base = 0.15 + 0.55 * (_i / max(1, len(resolved)))
            progress(base + 0.55 * p / max(1, len(resolved)), "fetch",
                     "{} {}".format(_name, msg))

        rows = []
        for (year_from, year_to) in buckets:
            for rec in sources_gbif.fetch_occurrences(
                key, year_from=year_from, year_to=year_to,
                max_records=per_bucket, progress=cb,
            ):
                rows.append({
                    **rec,
                    "dataset_id": GBIF_DATASET["id"],
                    "species_id": species_id,
                })
        total_inserted += _insert_occurrences(conn, rows)

    progress(0.72, "cells", "building 5x5 km cells")
    with conn.cursor() as cur:
        cur.execute(grid.SQL_BUILD_COARSE_CELLS.format(size=int(grid.CELL_SIZE_M)))
        cur.execute(grid.SQL_STAMP_OCCURRENCE_CELLS.format(size=int(grid.CELL_SIZE_M)))
    conn.commit()

    # ── Enrich evidence cells ────────────────────────────────────────────
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.cell_id,
                   ST_YMin(ST_Transform(c.geom_rd, 4326)) AS min_lat,
                   ST_XMin(ST_Transform(c.geom_rd, 4326)) AS min_lon,
                   ST_YMax(ST_Transform(c.geom_rd, 4326)) AS max_lat,
                   ST_XMax(ST_Transform(c.geom_rd, 4326)) AS max_lon
            FROM grid_cells c
            WHERE EXISTS (SELECT 1 FROM occurrences o WHERE o.cell_id = c.cell_id)
              AND NOT EXISTS (SELECT 1 FROM cell_environment e WHERE e.cell_id = c.cell_id)
            LIMIT %s
            """,
            (MAX_ENRICH_CELLS,),
        )
        cells = cur.fetchall()

    enriched_ok = 0
    for i, cell in enumerate(cells):
        ctx = sources_environment.enrich_cell(
            cell["min_lat"], cell["min_lon"], cell["max_lat"], cell["max_lon"])
        if ctx:
            db.upsert_cell_environment(conn, cell["cell_id"], {
                "forest_fraction": ctx.get("forest"),
                "broadleaf_fraction": None,
                "conifer_fraction": None,
                "heath_fraction": ctx.get("heath"),
                "wet_nature_fraction": ctx.get("wet_nature"),
                "tree_cover": ctx.get("tree_cover"),
                "canopy_height": ctx.get("canopy_height"),
                "microrelief": ctx.get("microrelief"),
                "groundwater_depth_cm": ctx.get("groundwater_depth_cm"),
                "soil_type": ctx.get("soil_type"),
                "soil_lime": ctx.get("soil_lime"),
                "protected": ctx.get("protected"),
                "path_density": ctx.get("path_density"),
            })
            enriched_ok += 1
        if cells:
            progress(0.75 + 0.15 * (i + 1) / len(cells), "environment",
                     "enriched {}/{} cells".format(i + 1, len(cells)))
    conn.commit()

    progress(0.92, "scoring", "recomputing cell scores")
    scored = pipeline_score.recompute(conn)
    db.mark_dataset_success(conn, GBIF_DATASET["id"])
    progress(1.0, "done", "historical refresh complete")

    return {
        "taxa_resolved": len(resolved),
        "occurrences_inserted": total_inserted,
        "cells_enriched": enriched_ok,
        "cell_scores": scored,
    }
