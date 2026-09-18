# Calibration and honest evaluation

The scoring in `worker/scoring.py` is a transparent expert prior, not a fitted
model. It is useful now and improvable later. This file records how to improve
it without fooling ourselves.

## What the current score means

```
static = weighted(recurrence, richness, habitat, moisture, access)
final  = static * (0.5 + 0.5 * condition) * season * confidence_factor
```

- `recurrence` is a detectability-adjusted occurrence index, **not occupancy**:
  open data has no absences and uneven effort.
- `effort` is a recording-volume proxy, explicitly labelled as such.
- `condition` is weather-driven (antecedent rain, dry spell, temperature band).
- `season` gates on the guild fruiting window.

Weights live in `worker/config.py`; bump `MODEL_VERSION` when they change.

## Known biases

1. **Observer bias.** Popular paths and car parks look better than remote,
   equally good forest. Path density is therefore a covariate, not a reward.
2. **Sampling scale.** Records are ~5 km. Cells with more observers, not more
   fungi, can rank higher.
3. **Missing predictors.** Tree species, deadwood, litter depth, soil pH and
   forest-floor humidity are not available nationally. 10 m targets are
   structural clues only.
4. **Data age.** AHN and tree cover can lag a storm, thinning or logging.

## How to validate (when field notes exist)

Collect, privately, per visit: date, candidate id, time spent, substrate/group
observed, and whether the visit produced useful photography. Include a few
**random control** locations so training is not self-confirming.

Then:

1. Fit an effort model first (distance to path, area, season).
2. Use presence-only methods with spatial thinning and target-group background.
3. Validate with **spatially blocked** and **yearly** holdouts, never random
   10 m folds.
4. Report the actionable metric: *"top 20% of cells captured X% of an
   independent year's useful finds"*, not AUC alone.
5. Run a weight sensitivity check: perturb weights by ±50% and confirm the
   ranking is stable. If it collapses, publish the component layers instead of
   a composite.

## Guardrails

- Exclude sensitive and Red List taxa from ranking, display and calibration.
- Never publish exact finds or move from coarse evidence to fine claims.
- Prefer existing paths; wet hollows and fragile habitat are easily damaged.
- Follow local access, reserve and collection rules; this app gives no legal
  permission.

## Review

The curated taxon list in `worker/config.py` is intentionally conservative and
must be reviewed by a mycologist (for example via the NMV) before it is treated
as authoritative.
