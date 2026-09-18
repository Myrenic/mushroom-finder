#!/usr/bin/env python3
"""Checks for the place-search and seasonal-ranking helpers.

These are the two pieces of logic the search-first UI depends on, so they get
a runnable check that does not need a database or network.

Run:  python3 tests/test_analytics.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

import datetime as dt  # noqa: E402

from analytics import (  # noqa: E402
    clean_query,
    query_variants,
    seasonal_score,
    week_of,
    weekly_profile,
    window,
)


def test_week_of_drives_the_projection():
    """The chosen date, not today, must decide the seasonal curve."""
    assert week_of(dt.date(2026, 9, 18)) == 38
    assert week_of(dt.date(2026, 10, 2)) == 40
    # The same calendar week a year earlier behaves identically.
    assert week_of(dt.date(2025, 9, 15)) == week_of(dt.date(2026, 9, 18)) == 38
    # Out-of-season dates still resolve, so the UI can say "nothing now".
    assert week_of(dt.date(2026, 1, 15)) == 3


def test_diminutive_and_article_relaxation():
    # The exact case that no geocoder indexes: "'t Nije Hemelriek" typed as
    # "hemelriekje". Both readings of the diminutive are offered, because
    # "hemelriekje" is hemelriek + je while "bloempje" is bloem + pje.
    assert query_variants("hemelriekje") == ["hemelriekje", "hemelriek", "hemelrie"]
    assert "bloem" in query_variants("bloempje")
    assert query_variants("'t Nije Hemelriek") == ["nije hemelriek"]
    assert query_variants("De Veluwe") == ["veluwe"]
    assert clean_query("het Amsterdamse Bos") == "amsterdamse bos"
    # Nothing to strip: one variant only, so search does not fan out.
    assert query_variants("Gasselte") == ["gasselte"]
    assert query_variants("") == []


def test_weekly_profile_and_window():
    arr = weekly_profile([10, 20, 30], [1, 2, 3])
    assert arr[9] == 1 and arr[19] == 2 and arr[29] == 3
    assert sum(arr) == 6
    # a window centred on week 20 covers weeks 19, 20, 21
    assert window(arr, 20) == 2.0
    # wraparound: week 1 covers 53, 1, 2
    assert window(arr, 1) == 0.0


def test_seasonal_score_peaks_at_one():
    # A species recorded only in weeks 39-41 must score ~1 there and 0 far away.
    arr = [0.0] * 53
    arr[38] = 5.0
    arr[39] = 10.0
    arr[40] = 4.0
    assert seasonal_score(arr, 40) > 0.99
    assert seasonal_score(arr, 20) == 0.0


def test_seasonal_score_takes_no_weather():
    """Guard the product decision: weather must not be an input to ranking."""
    import inspect

    sig = inspect.signature(seasonal_score)
    assert list(sig.parameters) == ["profile", "week"]
    for banned in ("rain", "temp", "weather", "precip", "condition"):
        assert banned not in sig.parameters


def test_empty_profile_is_safe():
    empty = [0.0] * 53
    assert seasonal_score(empty, 40) == 0.0
    assert window(empty, 1) == 0.0


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
