-- Provenance + precision upgrade.
--
-- Dutch Observation.org records are generalised to 5x5 km, but the same GBIF
-- query also returns precise datasets (iNaturalist is ~93% from 2020 onward).
-- We previously stored every record as the Observation.org dataset and
-- aggregated everything to 5 km, throwing that precision away.
--
-- `precise` marks records whose own coordinates are usable at ~1 km. Coarse
-- records keep feeding the 5 km evidence layer; precise records additionally
-- build `fine_cells` (1 km). Exact points are still never exposed.

ALTER TABLE occurrences ADD COLUMN IF NOT EXISTS source_dataset text;
ALTER TABLE occurrences ADD COLUMN IF NOT EXISTS precise boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS occurrences_precise_idx
  ON occurrences (precise, observed_on) WHERE precise;
CREATE INDEX IF NOT EXISTS occurrences_lastseen_idx ON occurrences (observed_on DESC);

CREATE TABLE IF NOT EXISTS fine_cells (
  cell_id    text NOT NULL,
  guild      text NOT NULL,
  n          integer NOT NULL DEFAULT 0,
  years      integer NOT NULL DEFAULT 0,
  first_seen date,
  last_seen  date,
  recent_n   integer NOT NULL DEFAULT 0,
  center_lat double precision,
  center_lon double precision,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (cell_id, guild)
);
CREATE INDEX IF NOT EXISTS fine_cells_recent_idx ON fine_cells (last_seen DESC);
CREATE INDEX IF NOT EXISTS fine_cells_geom_idx
  ON fine_cells (center_lat, center_lon);
