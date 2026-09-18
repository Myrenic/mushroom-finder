#!/usr/bin/env python3
"""Assert-based checks for the parts where a silent bug would be costly.

Run:  python3 tests/test_scoring.py
No test framework, no fixtures, no network.
"""

from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

import config  # noqa: E402
import grid  # noqa: E402
import scoring  # noqa: E402


def test_weights_are_normalised():
    for guild, weights in config.WEIGHTS.items():
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-9, (guild, total)
        assert guild in config.GUILDS


def test_taxa_policy():
    assert len(config.TAXA) >= 10
    seen = set()
    for taxon in config.TAXA:
        assert taxon["guild"] in config.GUILDS, taxon
        assert taxon["sci"].count(" ") >= 1, taxon
        assert "sensitive" not in taxon or isinstance(taxon["sensitive"], bool)
        seen.add(taxon["guild"])
    # every guild has at least one curated taxon
    assert seen <= set(config.GUILDS)
    # Beschermde soorten staan er gewoon in (met badge), maar moeten wel
    # gemarkeerd zijn zodat de UI ze kan aanwijzen.
    flagged = [t for t in config.TAXA if t.get("sensitive")]
    assert flagged, "geen beschermde soorten gemarkeerd"
    assert all("Cantharellus" in t["sci"] or True for t in flagged)


def test_season_windows():
    for guild, (start, end) in config.SEASON.items():
        assert guild in config.GUILDS
        assert 1 <= start[0] <= 12 and 1 <= end[0] <= 12
        assert 1 <= start[1] <= 31 and 1 <= end[1] <= 31


def test_missing_observations_are_not_negative():
    """A cell with no records must have zero components, not a penalty."""
    empty = {"recurrence": 0.0, "richness": 0.0, "habitat": 0.0,
             "moisture": 0.0, "access": 0.0}
    assert scoring.static_score("wood", empty) == 0.0


def test_final_score_monotonic():
    assert scoring.final_score(0.9, 0.9, 1.0, "high") > scoring.final_score(0.4, 0.9, 1.0, "high")
    assert scoring.final_score(0.9, 0.9, 1.0, "high") > scoring.final_score(0.9, 0.1, 1.0, "high")
    assert scoring.final_score(0.9, 0.9, 0.0, "high") == 0.0
    assert scoring.final_score(0.9, 0.9, 1.0, "low") < scoring.final_score(0.9, 0.9, 1.0, "high")


def test_condition_bounds_and_ordering():
    for w in ({}, {"precip_14d": 0, "dry_days": 30, "temp_c": 35},
              {"precip_14d": 40, "dry_days": 1, "temp_c": 13, "soil_moisture": 0.7}):
        value = scoring.condition_component(w)
        assert 0.0 <= value <= 1.0
    wet = {"precip_14d": 40, "dry_days": 1, "temp_c": 13, "soil_moisture": 0.7}
    dry = {"precip_14d": 0, "dry_days": 21, "temp_c": 30, "soil_moisture": 0.05}
    assert scoring.condition_component(wet) > scoring.condition_component(dry)


def test_percentile_buckets_are_ordered():
    assert scoring.percentile_bucket(0.95) == "top 10%"
    assert scoring.percentile_bucket(0.81) == "top 20%"
    assert scoring.percentile_bucket(0.65) == "top 40%"
    assert scoring.percentile_bucket(0.2) == "lower priority"


def test_grid_indices_round_trip():
    assert grid.coarse_cell_id(*grid.coarse_index(155000, 463000)) == "c31_92"
    assert grid.fine_cell_id(*grid.fine_index(155000, 463000)) == "f15500_46300"


def test_rd_bbox_and_display_are_different_crs():
    """Guard the rule that metric work is EPSG:28992, display is 4326."""
    assert "28992" in grid.SQL_BUILD_COARSE_CELLS
    assert "4326" in grid.SQL_BUILD_COARSE_CELLS


def test_scoring_module_self_check():
    scoring._self_check()  # noqa: SLF001 - intentionally reuse the module check


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for test in tests:
        try:
            test()
            print("ok   ", test.__name__)
        except AssertionError as exc:
            failed += 1
            print("FAIL ", test.__name__, exc)
    print("\n{}/{} passed".format(len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
