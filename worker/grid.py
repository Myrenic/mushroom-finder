"""Grid helpers for Mushroom Finder.

All metric work is EPSG:28992 (RD New). Display geometry is transformed to
EPSG:4326 by PostGIS, never measured there.

Two grids exist:

* coarse historical grid, 5x5 km, matching the resolution Dutch open
  observation data actually supports;
* fine candidate grid, 10x10 m, produced from environmental layers only.
  A fine cell is a *search target*, never a record interpolation.
"""

from __future__ import annotations

from config import CELL_SIZE_M, FINE_CELL_SIZE_M

# WGS84 bounding box for pre-filtering API queries.
NL_LATLON = {"min_lat": 50.70, "max_lat": 53.60, "min_lon": 3.30, "max_lon": 7.25}


def coarse_cell_id(ix: int, iy: int) -> str:
    return "c{}_{}".format(ix, iy)


def fine_cell_id(ix: int, iy: int) -> str:
    return "f{}_{}".format(ix, iy)


def coarse_index(x_rd: float, y_rd: float) -> tuple[int, int]:
    return int(x_rd // CELL_SIZE_M), int(y_rd // CELL_SIZE_M)


def fine_index(x_rd: float, y_rd: float) -> tuple[int, int]:
    return int(x_rd // FINE_CELL_SIZE_M), int(y_rd // FINE_CELL_SIZE_M)


# ---------------------------------------------------------------------------
# SQL fragments (kept as constants so they can be reused and reviewed).
# ---------------------------------------------------------------------------

# Upsert coarse cells for every occurrence that has no cell yet, then stamp
# occurrence.cell_id. Done in one round-trip, entirely in PostGIS, so no
# projection library is needed in Python.
SQL_BUILD_COARSE_CELLS = """
WITH pts AS (
    SELECT id, ST_Transform(geom, 28992) AS g
    FROM occurrences
    WHERE cell_id IS NULL AND geom IS NOT NULL
),
bounds AS (
    SELECT DISTINCT floor(ST_X(g) / {size})::bigint AS ix,
                    floor(ST_Y(g) / {size})::bigint AS iy
    FROM pts
)
INSERT INTO grid_cells (cell_id, geom_rd, center_lat, center_lon, area_ha)
SELECT 'c' || b.ix || '_' || b.iy,
       env.geom,
       ST_Y(ST_Transform(ST_Centroid(env.geom), 4326)),
       ST_X(ST_Transform(ST_Centroid(env.geom), 4326)),
       {size} * {size} / 10000.0
FROM bounds b
CROSS JOIN LATERAL (
    SELECT ST_MakeEnvelope(b.ix * {size}, b.iy * {size},
                           b.ix * {size} + {size},
                           b.iy * {size} + {size}, 28992) AS geom
) env
ON CONFLICT (cell_id) DO NOTHING;
"""

SQL_STAMP_OCCURRENCE_CELLS = """
UPDATE occurrences o
SET cell_id = 'c' || floor(ST_X(ST_Transform(o.geom, 28992)) / {size})::bigint
              || '_' || floor(ST_Y(ST_Transform(o.geom, 28992)) / {size})::bigint
WHERE o.cell_id IS NULL AND o.geom IS NOT NULL;
"""

# Classify stored records as precise (usable near 1 km) or coarse (5 km).
# iNaturalist publishes exact coordinates; Observation.org NL does not.
SQL_BACKFILL_PRECISION = """
UPDATE occurrences SET
    source_dataset = coalesce(source_dataset, raw->>'datasetKey'),
    precise = (
        (coord_uncertainty_m IS NOT NULL AND coord_uncertainty_m <= 1000)
        OR (coord_uncertainty_m IS NULL
            AND raw->>'datasetKey' = %(inat)s
            AND (raw->>'informationWithheld') IS NULL)
    )
WHERE source_dataset IS NULL;
"""

# Rebuild recent reports from every usable record in the last ~13 months.
# Coarse records are snapped to their 5 km cell centre, precise ones to 1 km.
SQL_REBUILD_RECENT_REPORTS = """
DELETE FROM recent_reports;
INSERT INTO recent_reports (id, guild, species_id, name_nl, scientific_name,
                            observed_on, resolution_m, precise, sensitive, lat, lon)
SELECT DISTINCT ON (x.species_id, x.observed_on, x.res, x.lat, x.lon, x.sensitive)
       md5(x.species_id || '|' || x.observed_on || '|' || x.res || '|' ||
           x.lat || '|' || x.lon),
       x.guild, x.species_id, x.name_nl, x.scientific_name, x.observed_on,
       x.res, x.precise, x.sensitive, x.lat, x.lon
FROM (
    SELECT s.id AS species_id, s.name_nl, s.scientific_name, s.guild,
           o.observed_on, false AS precise, 5000 AS res, s.sensitive,
           gc.center_lat AS lat, gc.center_lon AS lon
    FROM occurrences o
    JOIN species s ON s.id = o.species_id
    JOIN grid_cells gc ON gc.cell_id = o.cell_id
    WHERE NOT o.precise AND s.enabled
      AND o.observed_on >= current_date - 400
    UNION ALL
    SELECT s.id, s.name_nl, s.scientific_name, s.guild, o.observed_on,
           true, 1000, s.sensitive,
           ST_Y(ST_Transform(ST_Centroid(e.env), 4326)),
           ST_X(ST_Transform(ST_Centroid(e.env), 4326))
    FROM occurrences o
    JOIN species s ON s.id = o.species_id
    CROSS JOIN LATERAL (
        SELECT ST_MakeEnvelope(
                   floor(ST_X(ST_Transform(o.geom, 28992)) / 1000) * 1000,
                   floor(ST_Y(ST_Transform(o.geom, 28992)) / 1000) * 1000,
                   floor(ST_X(ST_Transform(o.geom, 28992)) / 1000) * 1000 + 1000,
                   floor(ST_Y(ST_Transform(o.geom, 28992)) / 1000) * 1000 + 1000,
                   28992) AS env
    ) e
    WHERE o.precise AND s.enabled
      AND o.observed_on >= current_date - 400
) x
WHERE x.lat IS NOT NULL AND x.lon IS NOT NULL;
"""

# Per (cell, guild): records, richness, recurrence proxy, last seen.
SQL_CELL_GUILD_AGG = """
WITH taxon_guild AS (
    SELECT s.id AS species_id, s.guild
    FROM species s
    WHERE s.enabled AND NOT s.sensitive
),
obs AS (
    SELECT o.cell_id, t.guild, o.species_id, o.observed_on
    FROM occurrences o
    JOIN taxon_guild t ON t.species_id = o.species_id
    WHERE o.cell_id IS NOT NULL
),
per_species AS (
    SELECT cell_id, guild, species_id,
           count(*) AS n,
           count(DISTINCT date_part('year', observed_on)) AS years,
           max(observed_on) AS last_seen
    FROM obs
    GROUP BY cell_id, guild, species_id
)
SELECT cell_id, guild,
       sum(n)::int          AS records,
       count(*)::int        AS richness,
       max(last_seen)       AS last_seen,
       avg(years)::real     AS avg_years
FROM per_species
GROUP BY cell_id, guild;
"""
