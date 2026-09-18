-- Beschermde soorten mogen in de app zichtbaar zijn, maar wel herkenbaar.
-- De vlag gaat mee naar de recente meldingen zodat de UI een badge kan tonen
-- in plaats van de soort weg te laten.

ALTER TABLE recent_reports ADD COLUMN IF NOT EXISTS sensitive boolean NOT NULL DEFAULT false;
