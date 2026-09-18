# Mushroom Finder

A private, household-only web app that ranks where in the Netherlands it is
worth searching for macro fungi — for photography, not for picking.

It combines open Dutch data into two clearly separated layers:

| Layer | Resolution | Meaning |
|---|---|---|
| Historical evidence | 5 x 5 km | Where a mushroom group is repeatedly recorded. Broad, effort-biased, honest. |
| 10 m search targets | 10 x 10 m | Experimental habitat clues from AHN lidar, canopy and mapped habitat. A place to look, never a mushroom location. |

Everything is dry, deterministic and explainable: no machine learning, no
claims of exact occurrence, no sensitive-species locations.

## What it is not

- Not a foraging or collection tool.
- Not a source of exact mushroom coordinates.
- Not a public website (behind Keycloak via oauth2-proxy).
- Not a photo library (v1 finds places only).

## Architecture

```
Browser (React/Vite/Leaflet)
  -> Traefik -> oauth2-proxy (Keycloak)
    -> nginx (static SPA) + /api -> api/server.py
                                    |- PostGIS (provenance, zones, candidates)
                                    |- Kubernetes batch/v1 Jobs (refresh)
Worker Jobs (python:3.12-slim + pip deps)
  |- GBIF / Observation.org  (historical records, coarse)
  |- PDOK AHN WCS            (0.5 m DTM/DSM -> 10 m)
  |- OSM Overpass            (habitat + path proxies)
  |- Open-Meteo              (antecedent rainfall / temperature)
```

All metric work is EPSG:28992. Rasters are never stored in PostGIS; see
`docs/storage.md`.

## Data sources (all keyless in v1)

| Source | Use | Licence |
|---|---|---|
| GBIF (Observation.org dataset, DOI 10.15468/5nilie) | historical fungi records, ~5 km generalised | CC BY-NC 4.0 |
| PDOK AHN (`dtm_05m`, `dsm_05m` WCS) | canopy + microrelief for 10 m targets | open, attribution |
| PDOK BRT-Achtergrondkaart | basemap tiles | CC BY 4.0 |
| PDOK Natura 2000 (OGC API) | protection flag / access context | open |
| OpenStreetMap (Overpass) | habitat fractions, path density | ODbL |
| Open-Meteo | antecedent rainfall, soil moisture/temp | CC BY 4.0, non-commercial |

KNMI's own open data would be the authoritative Dutch weather source but needs
a free API key; Open-Meteo keeps v1 keyless. A KNMI adapter can be added behind
the same interface later.

## Prerequisites (one-time, out of band)

The database password is **not** in Git. Create the Secret the same way
`keycloak-webui-oauth` is handled:

```bash
kubectl -n services create secret generic mushroom-finder-db \
  --from-literal=password="$(openssl rand -base64 24)"
```

## Build

```bash
# worker + api + migrations ConfigMaps
node scripts/build-worker-configmaps.mjs

# SPA bundle (writes base/www + base/webui.configmap.json)
cd webui && npm install && npm run build && cd ..

# tests
python3 tests/test_scoring.py

# manifest validation
kubectl kustomize kubernetes/apps >/dev/null
```

## Deploy (Flux)

```bash
git add -A kubernetes/apps/services/mushroom-finder \
          kubernetes/apps/services/kustomization.yaml \
          kubernetes/apps/network/ingressroutes/mushroom-finder.yaml \
          kubernetes/apps/network/ingressroutes/kustomization.yaml
git commit -m "feat(mushroom-finder): add mushroom search-priority app"
flux reconcile kustomization mushroom-finder -n flux-system --with-source
kubectl -n services rollout status deploy/mushroom-finder --timeout=180s
kubectl -n services rollout status deploy/mushroom-finder-postgres --timeout=180s
```

ConfigMap changes do not hot-reload: `kubectl -n services rollout restart
deploy/mushroom-finder` after rebuilding.

The app is served at `https://mushrooms.${SECRET_DOMAIN_0}`.

## First run

The API applies migrations on startup. Data arrives via the two UI buttons or
the CronJobs:

- **Weather** (button / `mushroom-weather`, every 3 h): current conditions.
- **Historical** (button / `mushroom-historical`, monthly): GBIF records,
  coarse cells, environment enrichment, static scores.
- **10 m now** (button, per visible area, max 2 km): on-demand AHN analysis.
- `mushroom-maintenance` (daily): stale-run recovery, score recompute, pruning.

The first historical run downloads up to 20k records per curated taxon and can
take a few minutes.

## Operational notes

- Worker Jobs install Python deps at start (no custom registry), bounded to a
  3 GiB limit. National raster work is windowed; never allocate national arrays.
- Jobs are server-generated. The browser only ever sends `weather`,
  `historical`, or a validated bbox + guild.
- One run per kind is enforced by a partial unique index; stale runs are reaped
  after 45 minutes without a heartbeat.
- `docs/storage.md`, `docs/resolution.md` and `docs/calibration.md` record the
  design rules, scale semantics and how to validate the model with field notes.

## Follow-ups (deliberately not in v1)

- JWT (JWKS) verification instead of trusting forwarded identity headers.
- A national 10 m COG build (25 m screen -> 10 m output) instead of only
  on-demand AOI, plus a raster overlay for the map.
- BRO Bodemkaart / WDM and RIVM tree cover as richer substrate/moisture priors.
- A private field-observation workflow (effort-aware) to calibrate weights.
