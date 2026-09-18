"""Transparent scoring for Mushroom Finder.

Pure functions only: no database, no network, no globals beyond config.
This is the one place a silent bug would be costly, so every branch here is
covered by tests/test_scoring.py and the __main__ self-check below.

Vocabulary (kept deliberately honest):
  static_score  -> habitat/history potential of a coarse cell for a guild
  condition     -> current weather suitability ("fruiting flush" conditions)
  season        -> where we are in the guild's fruiting window
  fsp           -> field search priority percentile for a 10 m candidate
"""

from __future__ import annotations

import datetime as dt
import math

from config import MODEL_VERSION, SEASON, WEIGHTS

CONFIDENCE_FACTOR = {"high": 1.0, "medium": 0.85, "low": 0.6}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _band(value: float, lo: float, hi: float, soft: float) -> float:
    """1.0 inside [lo, hi], fading to 0.0 `soft` units outside it."""
    if lo <= value <= hi:
        return 1.0
    if value < lo:
        return _clamp(1.0 - (lo - value) / soft)
    return _clamp(1.0 - (value - hi) / soft)


# ── Season ────────────────────────────────────────────────────────────────
def season_factor(guild: str, day: dt.date) -> float:
    """0 outside the fruiting window, 1 at its centre."""
    start, end = SEASON.get(guild, ((1, 1), (12, 31)))
    s = dt.date(day.year, start[0], start[1])
    e = dt.date(day.year, end[0], end[1])
    if e < s:  # wraps the new year
        if day < s and day > e:
            return 0.0
        span = ((e + dt.timedelta(days=365)) - s).days
        pos = ((day if day >= s else day + dt.timedelta(days=365)) - s).days
    else:
        if day < s or day > e:
            return 0.0
        span = (e - s).days
        pos = (day - s).days
    if span <= 0:
        return 1.0
    return 1.0 - abs(pos / span * 2.0 - 1.0)


def in_season(guild: str, day: dt.date) -> bool:
    return season_factor(guild, day) > 0.0


# ── Historical components ─────────────────────────────────────────────────
def recurrence_component(avg_years: float, window_years: int = 7) -> float:
    """Average number of distinct years a guild was recorded, normalised.

    This is a *detectability-adjusted occurrence index*, not occupancy: open
    data has no absences and uneven effort.
    """
    if window_years <= 0:
        raise ValueError("window_years must be positive")
    return _clamp(avg_years / window_years)


def effort_component(records: int, saturation: int = 400) -> float:
    """Recording-effort proxy (distinct record volume). Documented as a proxy."""
    if records <= 0:
        return 0.0
    return _clamp(math.log1p(records) / math.log1p(saturation))


def richness_component(richness: int, saturation: int = 12) -> float:
    return _clamp(richness / saturation)


# ── Environmental components (from cell_environment) ─────────────────────
def habitat_component(env: dict, guild: str) -> float:
    """Habitat suitability 0..1 per guild from PDOK land cover / terrain."""
    forest = env.get("forest_fraction") or 0.0
    heath = env.get("heath_fraction") or 0.0
    wet = env.get("wet_nature_fraction") or 0.0
    tree_cover = env.get("tree_cover") or 0.0

    # There is no open national tree-species layer. When the host split is
    # unknown, use forest presence itself: we know a stand exists, not what
    # is in it. This is a deliberate, documented approximation.
    broadleaf = env.get("broadleaf_fraction")
    conifer = env.get("conifer_fraction")
    if broadleaf is None and conifer is None:
        broadleaf = conifer = forest
    else:
        broadleaf = broadleaf or 0.0
        conifer = conifer or 0.0

    if guild == "wood":
        return _clamp(0.55 * forest + 0.30 * tree_cover + 0.15 * min(1.0, broadleaf / 0.5))
    if guild == "mycorrhizal":
        return _clamp(0.45 * forest + 0.25 * tree_cover + 0.15 * max(broadleaf, conifer) + 0.15 * min(1.0, (broadleaf + conifer) / 0.6))
    if guild == "litter":
        return _clamp(0.60 * broadleaf + 0.25 * tree_cover + 0.15 * forest)
    if guild == "wet":
        return _clamp(0.55 * wet + 0.30 * forest + 0.15 * tree_cover)
    if guild == "grassland":
        return _clamp(0.55 * heath + 0.45 * (1.0 - min(1.0, forest / 0.6)))
    return 0.35


def moisture_component(env: dict) -> float:
    """Durable wetness 0..1. Shallower groundwater maps to wetter habitat."""
    depth = env.get("groundwater_depth_cm")
    wet_nature = env.get("wet_nature_fraction") or 0.0
    micro = env.get("microrelief") or 0.0
    if depth is None:
        base = 0.4
    else:
        # 20 cm depth -> ~1.0, 200 cm -> ~0.1
        base = _clamp(1.0 - (depth - 20.0) / 180.0)
    return _clamp(0.6 * base + 0.25 * wet_nature + 0.15 * min(1.0, micro * 4.0))


def access_component(env: dict) -> float:
    """How practical a cell is to visit. Rewards nearby paths, never claims
    that leaving them is legal."""
    path_density = env.get("path_density") or 0.0
    protected = env.get("protected")
    base = _band(path_density, 0.05, 0.6, 0.6)
    if protected:
        base *= 0.85
    return _clamp(base)


# ── Static score ──────────────────────────────────────────────────────────
def static_score(guild: str, components: dict) -> float:
    weights = WEIGHTS.get(guild)
    if not weights:
        raise ValueError("unknown guild: " + guild)
    values = {
        "recurrence": components.get("recurrence", 0.0),
        "richness": components.get("richness", 0.0),
        "habitat": components.get("habitat", 0.0),
        "moisture": components.get("moisture", 0.0),
        "access": components.get("access", 0.0),
    }
    total = sum(weights[k] * _clamp(values[k]) for k in weights)
    return _clamp(total)


def confidence(effort: float, recurrence: float, records: int) -> str:
    if records >= 25 and effort >= 0.65 and recurrence >= 0.5:
        return "high"
    if records >= 8 and effort >= 0.30:
        return "medium"
    return "low"


# ── Current conditions ────────────────────────────────────────────────────
def condition_component(w: dict) -> float:
    """Weather-driven fruiting suitability 0..1.

    Uses antecedent rainfall (fruiting responds to multi-day wetness, not
    same-day weather) plus a temperature band and forecast rain.
    """
    p14 = w.get("precip_14d") or 0.0
    dry_days = w.get("dry_days") or 0
    temp = w.get("temp_c")
    soil_moisture = w.get("soil_moisture")
    forecast = w.get("forecast_precip_3d") or 0.0

    rain = _band(p14, 15.0, 70.0, 30.0)
    dryness = 1.0 - _clamp(max(0.0, dry_days - 3) / 14.0)
    heat = _band(temp, 8.0, 18.0, 10.0) if temp is not None else 0.5
    soil = _clamp(soil_moisture) if soil_moisture is not None else 0.5
    ahead = _clamp(forecast / 10.0)

    return _clamp(0.35 * rain + 0.20 * dryness + 0.20 * heat + 0.15 * soil + 0.10 * ahead)


def final_score(static: float, condition: float, season: float, conf: str) -> float:
    """Combine into a 0..1 photo-potential score.

    Season gates (no fruiting window -> nothing to find); weather modulates;
    confidence scales down uncertain cells rather than pretending precision.
    """
    factor = CONFIDENCE_FACTOR.get(conf, 0.6)
    return _clamp(static * (0.5 + 0.5 * _clamp(condition)) * _clamp(season) * factor)


def percentile_bucket(fsp: float) -> str:
    """Turn a 0..1 priority into a human bucket; never a fake percentage."""
    if fsp >= 0.90:
        return "top 10%"
    if fsp >= 0.80:
        return "top 20%"
    if fsp >= 0.60:
        return "top 40%"
    return "lower priority"


def _self_check() -> None:
    assert recurrence_component(0.0) == 0.0
    assert recurrence_component(7.0) == 1.0
    assert recurrence_component(14.0) == 1.0
    assert effort_component(0) == 0.0
    assert 0.0 < effort_component(10) < 1.0

    summer = dt.date(2026, 6, 21)
    peak = dt.date(2026, 9, 23)  # centre of the mycorrhizal window
    assert season_factor("mycorrhizal", summer) == 0.0
    assert season_factor("mycorrhizal", peak) > 0.98
    assert 0.0 < season_factor("mycorrhizal", dt.date(2026, 11, 10)) < 1.0
    assert in_season("wood", peak)

    env = {
        "forest_fraction": 0.8,
        "broadleaf_fraction": 0.6,
        "tree_cover": 0.7,
        "groundwater_depth_cm": 40.0,
        "path_density": 0.3,
        "protected": False,
    }
    assert habitat_component(env, "wood") > 0.5
    assert moisture_component(env) > 0.5
    assert 0.0 < access_component(env) <= 1.0

    components = {"recurrence": 0.8, "richness": 0.5, "habitat": 0.7,
                  "moisture": 0.6, "access": 0.5}
    s = static_score("wood", components)
    assert 0.0 <= s <= 1.0

    wet = {"precip_14d": 40.0, "dry_days": 1, "temp_c": 13.0,
           "soil_moisture": 0.7, "forecast_precip_3d": 8.0}
    dry = {"precip_14d": 0.0, "dry_days": 21, "temp_c": 30.0,
           "soil_moisture": 0.05, "forecast_precip_3d": 0.0}
    assert condition_component(wet) > condition_component(dry)

    assert final_score(1.0, 1.0, 1.0, "high") == 1.0
    assert final_score(1.0, 1.0, 0.0, "high") == 0.0
    assert percentile_bucket(0.95) == "top 10%"
    assert MODEL_VERSION


if __name__ == "__main__":
    _self_check()
    print("scoring self-check OK")
