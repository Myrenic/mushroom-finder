-- Mushroom Finder schema.
--
-- Design rules:
--   * Occurrence geometry is stored generalised (Dutch GBIF/Observation.org
--     records are ~5x5 km). It is never exposed below the coarse cell.
--   * Metric work happens in EPSG:28992; display geometry is 4326.
--   * Rasters never live in PostGIS (see docs/storage.md); only vectors here.
--
-- The API applies this file on startup, so every statement is idempotent.

CREATE EXTENSION IF NOT EXISTS postgis;

-- ── Provenance ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS datasets (
  id              text PRIMARY KEY,
  provider        text NOT NULL,
  title           text NOT NULL,
  source_url      text,
  licence         text,
  attribution     text,
  version         text,
  last_success_at timestamptz,
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS attributions (
  id         text PRIMARY KEY,
  label      text NOT NULL,
  url        text,
  licence    text,
  required   boolean NOT NULL DEFAULT true,
  sort_order integer NOT NULL DEFAULT 100
);

-- ── Curated taxa ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS species (
  id             text PRIMARY KEY,
  taxon_key      bigint,
  scientific_name text NOT NULL,
  name_nl        text,
  guild          text NOT NULL,
  phenology      jsonb NOT NULL DEFAULT '{}'::jsonb,
  photo_value    smallint NOT NULL DEFAULT 3,
  sensitive      boolean NOT NULL DEFAULT false,
  enabled        boolean NOT NULL DEFAULT true,
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS species_guild_idx ON species (guild) WHERE enabled;

-- ── Coarse occurrences (generalised) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS occurrences (
  id                  bigserial PRIMARY KEY,
  dataset_id          text REFERENCES datasets(id),
  source_id           text NOT NULL,
  species_id          text REFERENCES species(id),
  observed_on         date,
  cell_id             text,
  geom                geometry(Point, 4326),
  coord_uncertainty_m integer,
  generalized         boolean NOT NULL DEFAULT true,
  licence             text,
  raw                 jsonb,
  UNIQUE (dataset_id, source_id)
);
CREATE INDEX IF NOT EXISTS occurrences_geom_idx ON occurrences USING gist (geom);
CREATE INDEX IF NOT EXISTS occurrences_cell_idx ON occurrences (cell_id);
CREATE INDEX IF NOT EXISTS occurrences_species_idx ON occurrences (species_id, observed_on);

-- ── 5x5 km historical cells ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS grid_cells (
  cell_id    text PRIMARY KEY,
  geom_rd    geometry(Polygon, 28992) NOT NULL,
  center_lat double precision,
  center_lon double precision,
  area_ha    double precision
);
CREATE INDEX IF NOT EXISTS grid_cells_geom_idx ON grid_cells USING gist (geom_rd);

-- ── Environmental context per coarse cell (from PDOK / OSM adapters) ─────
CREATE TABLE IF NOT EXISTS cell_environment (
  cell_id             text PRIMARY KEY REFERENCES grid_cells(cell_id) ON DELETE CASCADE,
  forest_fraction     real,
  broadleaf_fraction  real,
  conifer_fraction    real,
  heath_fraction      real,
  wet_nature_fraction real,
  tree_cover          real,
  canopy_height       real,
  microrelief         real,
  groundwater_depth_cm real,
  soil_type           text,
  soil_lime           text,
  protected           boolean,
  path_density        real,
  updated_at          timestamptz NOT NULL DEFAULT now()
);

-- ── Static per-cell, per-guild scores ────────────────────────────────────
CREATE TABLE IF NOT EXISTS cell_scores (
  cell_id     text NOT NULL REFERENCES grid_cells(cell_id) ON DELETE CASCADE,
  guild       text NOT NULL,
  recurrence  real,
  richness    integer,
  last_seen   date,
  effort      real,
  habitat     real,
  moisture    real,
  access      real,
  static_score real,
  confidence  text NOT NULL DEFAULT 'low',
  components  jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (cell_id, guild)
);
CREATE INDEX IF NOT EXISTS cell_scores_score_idx ON cell_scores (static_score DESC);

-- ── 10 m candidate search sites (capped top-K, clustered) ────────────────
CREATE TABLE IF NOT EXISTS candidate_sites (
  id         text PRIMARY KEY,
  cell_id    text,
  guild      text NOT NULL,
  geom       geometry(Point, 4326),
  geom_rd    geometry(Point, 28992),
  fsp        real NOT NULL,           -- field search priority percentile 0..1
  confidence text NOT NULL DEFAULT 'C',
  components jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS candidate_sites_geom_idx ON candidate_sites USING gist (geom);
CREATE INDEX IF NOT EXISTS candidate_sites_fsp_idx ON candidate_sites (fsp DESC);

-- ── Weather / current fruiting conditions ────────────────────────────────
CREATE TABLE IF NOT EXISTS weather_snapshots (
  id                  bigserial PRIMARY KEY,
  scope               text NOT NULL,      -- 'nl' or a cell_id
  as_of               timestamptz NOT NULL,
  precip_7d           real,
  precip_14d          real,
  precip_21d          real,
  dry_days            integer,
  temp_c              real,
  soil_temp_c         real,
  soil_moisture       real,
  forecast_precip_3d  real,
  condition_score     real,
  raw                 jsonb,
  UNIQUE (scope, as_of)
);
CREATE INDEX IF NOT EXISTS weather_snapshots_scope_idx ON weather_snapshots (scope, as_of DESC);

-- ── Refresh runs (manual + scheduled) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS refresh_runs (
  id           text PRIMARY KEY,
  kind         text NOT NULL,
  status       text NOT NULL DEFAULT 'queued',
  requested_by text,
  progress     real NOT NULL DEFAULT 0,
  phase        text,
  message      text,
  started_at   timestamptz NOT NULL DEFAULT now(),
  heartbeat_at timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  params       jsonb NOT NULL DEFAULT '{}'::jsonb,
  stats        jsonb,
  error        text
);
-- At most one queued/running run per kind; the INSERT is the lock.
CREATE UNIQUE INDEX IF NOT EXISTS refresh_runs_one_active
  ON refresh_runs (kind) WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS refresh_runs_kind_idx ON refresh_runs (kind, started_at DESC);

-- ── Layer versions / AOI cache ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS layer_versions (
  layer          text PRIMARY KEY,
  version        text NOT NULL,
  resolution_m   integer,
  extent         text,
  storage_path   text,
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS aoi_cache (
  id           text PRIMARY KEY,          -- hash of bbox + layers version + model version
  bbox_rd      double precision[] NOT NULL,
  guild        text,
  layer_version text,
  model_version text,
  storage_path text,
  byte_size    bigint,
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz NOT NULL DEFAULT now()
);
