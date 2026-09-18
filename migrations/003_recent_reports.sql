-- Recent reports across all records, not just the precise ones.
--
-- Observation.org NL is generalised to 5 km but it is 99% of Dutch volume;
-- showing only precise (iNaturalist) records made the recent layer look empty.
-- Keep both, and carry the resolution so the UI can be honest about it.
--
-- Display coordinates are cell centres: 1 km for precise records, 5 km for
-- generalised ones. Exact occurrence points are still never exposed.

CREATE TABLE IF NOT EXISTS recent_reports (
  id              text PRIMARY KEY,
  guild           text NOT NULL,
  species_id      text,
  name_nl         text,
  scientific_name text,
  observed_on     date NOT NULL,
  resolution_m    integer NOT NULL,
  precise         boolean NOT NULL DEFAULT false,
  lat             double precision,
  lon             double precision
);
CREATE INDEX IF NOT EXISTS recent_reports_date_idx ON recent_reports (observed_on DESC);
CREATE INDEX IF NOT EXISTS recent_reports_guild_idx ON recent_reports (guild);
