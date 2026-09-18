"""Recompute static per-cell, per-guild scores from stored data.

Split from the GBIF import so scoring changes (config.py) can be applied
without re-downloading anything.
"""

from __future__ import annotations

import grid
import scoring
from config import INATURALIST_DATASET, MODEL_VERSION


def _load_environment(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM cell_environment")
        return {row["cell_id"]: row for row in cur.fetchall()}


def rebuild_fine_cells(conn) -> int:
    """Rebuild the 1 km precise-record layer (exact points never leave the DB)."""
    with conn.cursor() as cur:
        cur.execute(grid.SQL_BACKFILL_PRECISION, {"inat": INATURALIST_DATASET})
        cur.execute(grid.SQL_REBUILD_FINE_CELLS)
        cur.execute(grid.SQL_REBUILD_RECENT_REPORTS)
        cur.execute("SELECT count(*) AS n FROM fine_cells")
        count = cur.fetchone()["n"]
    conn.commit()
    return count


def recompute(conn) -> int:
    rebuild_fine_cells(conn)
    with conn.cursor() as cur:
        cur.execute(grid.SQL_CELL_GUILD_AGG)
        rows = cur.fetchall()

    env_by_cell = _load_environment(conn)
    out = []
    for row in rows:
        env = env_by_cell.get(row["cell_id"]) or {}
        records = row["records"] or 0
        richness = row["richness"] or 0
        avg_years = float(row["avg_years"] or 0)

        eff = scoring.effort_component(records)
        components = {
            "recurrence": scoring.recurrence_component(avg_years),
            "richness": scoring.richness_component(richness),
            "habitat": scoring.habitat_component(env, row["guild"]),
            "moisture": scoring.moisture_component(env),
            "access": scoring.access_component(env),
        }
        static = scoring.static_score(row["guild"], components)
        conf = scoring.confidence(eff, components["recurrence"], records)
        out.append({
            "cell_id": row["cell_id"],
            "guild": row["guild"],
            "recurrence": components["recurrence"],
            "richness": richness,
            "last_seen": row["last_seen"],
            "effort": eff,
            "habitat": components["habitat"],
            "moisture": components["moisture"],
            "access": components["access"],
            "static_score": static,
            "confidence": conf,
            "components": {**components, "model_version": MODEL_VERSION},
        })

    if out:
        import db
        db.upsert_cell_scores(conn, out)
        conn.commit()
    return len(out)
