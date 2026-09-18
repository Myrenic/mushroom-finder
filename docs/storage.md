# Storage model

## PostGIS (backed up)

Vector truth and provenance only:

- `datasets`, `attributions` - what data, from where, under which licence.
- `species` - the curated, non-sensitive taxon policy.
- `occurrences` - generalised records (coarse point + uncertainty + licence).
- `grid_cells`, `cell_environment`, `cell_scores` - the 5 km evidence layer.
- `candidate_sites` - capped set of 10 m search targets (top-K per analysis).
- `weather_snapshots` - antecedent conditions.
- `refresh_runs` - progress, freshness, one-active-run-per-kind.
- `layer_versions`, `aoi_cache` - model/data versioning and area caching.

Volume: 10 GiB on `longhorn-2-replicas`, PVC `mushroom-finder-postgres`.

## Rasters (not yet stored)

National rasters are **not** kept in PostGIS. The current 10 m pipeline is
on-demand: AHN windows are fetched from PDOK WCS, aggregated in the worker, and
only the resulting candidate points are persisted. Nothing national is cached.

When a national 10 m COG build is added, it must go on a separate,
recreatable Longhorn volume (`longhorn-1-replica`), be excluded from Velero,
and keep at most two versions. It must never become database rows: a national
10 m grid is ~339 M cells of land (about 910 M across the RD rectangle), which
is fine as compressed raster and fatal as a table or browser features.

## Never in Git

Source downloads, GBIF archives, GeoTIFFs, database dumps, or anything from
`base/www` other than the built SPA artifacts the repo already commits.

## Backups

- Add a Velero schedule labelled `app.kubernetes.io/name: mushroom-finder`.
- Add a logical `pg_dump` to the existing off-cluster target and **test a
  restore** before trusting the database. A single-node Longhorn volume is not
  a backup.
- Raster/AOI caches are recreatable and should be excluded.
