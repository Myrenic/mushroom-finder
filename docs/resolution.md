# Resolution semantics

Four different things are called "resolution". Keeping them separate is what
makes the app honest.

| Term | Value here | Meaning |
|---|---|---|
| Display / candidate cell | **10 m** | The size of a "go and look here" square. |
| Predictor native resolution | 0.5 m - 50 m | AHN 0.5 m, LGN 5 m, tree cover 10 m, groundwater 50 m. |
| Effective habitat confidence | ~25-250 m | What the inputs can actually support once combined. |
| Occurrence claim | **none** | No statement that a fruiting body is at a cell. |

## Rules

1. **Historical labels stay coarse.** Dutch open fungi records are generalised
   to about 5 x 5 km. They are aggregated into 5 km cells and never
   interpolated to a finer grid.
2. **Fine targets come only from environment.** A 10 m target is built from AHN
   terrain/canopy and mapped habitat within the selected area. It is never
   derived from an observation coordinate.
3. **All metric geometry is EPSG:28992.** Grids, distances and areas are RD New.
   EPSG:4326 is display only; EPSG:3857 is basemap imagery only.
4. **Weather is coarse.** Antecedent rainfall and temperature modulate a whole
   area. They are not interpolated to individual 10 m cells.
5. **Numbers are buckets, not probabilities.** The UI shows search priority
   percentiles ("top 10/20/40%") and confidence grades, never "87% chance of
   mushrooms".

## Confidence grades

- **A** - direct terrain/structure measurement (AHN-derived).
- **B** - derived habitat proxy (land cover, canopy, mapped habitat).
- **C** - coarse assumption (soil, groundwater, tree host, deadwood).

A 10 m target is at best grade B. Deadwood, host species, litter depth, pH and
forest-floor humidity are not observable from the open data used here.
