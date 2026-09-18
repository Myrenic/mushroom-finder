-- Species photos.
--
-- Sourced from Wikipedia/Wikimedia Commons, which are freely licensed, and
-- NOT from Observation.org or iNaturalist: their observation photos are
-- CC BY-NC-ND and must not be redistributed. Each row keeps a link to the
-- Commons file page so the individual credit and licence stay one click away.

ALTER TABLE species ADD COLUMN IF NOT EXISTS image_url text;
ALTER TABLE species ADD COLUMN IF NOT EXISTS image_credit text;
ALTER TABLE species ADD COLUMN IF NOT EXISTS image_credit_url text;
