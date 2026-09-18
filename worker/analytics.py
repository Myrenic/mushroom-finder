"""Pure helper functions shared by the API and the tests.

Kept free of database and network imports so they can be unit tested without
a running stack.
"""

from __future__ import annotations

import datetime as dt
import re

# Dutch articles and diminutive suffixes. Small places such as
# "'t Nije Hemelriek" are frequently searched with a diminutive
# ("hemelriekje"), which no geocoder indexes.
#
# The diminutive is ambiguous: "hemelriekje" is hemelriek + je, but
# "bloempje" is bloem + pje. Both readings are generated and tried in order
# rather than guessing.
_LEADING = re.compile("^['\u2019`]?t\\s+")
_ARTICLE = re.compile(r"^(de|het|een)\s+")
_SUFFIX_JE = re.compile(r"je$")
_SUFFIX_TJE = re.compile(r"(tje|kje|pje)$")


def clean_query(q: str) -> str:
    """Lowercase and drop leading Dutch articles."""
    s = (q or "").strip().lower()
    s = _LEADING.sub("", s)
    s = _ARTICLE.sub("", s)
    return s.strip()


def query_variants(q: str) -> list[str]:
    """Ordered, de-duplicated search candidates for a place query."""
    base = clean_query(q)
    if not base:
        return []
    out = [base]
    for variant in (_SUFFIX_JE.sub("", base), _SUFFIX_TJE.sub("", base)):
        variant = variant.strip()
        if variant and variant not in out:
            out.append(variant)
    return out





def week_of(d: dt.date) -> int:
    """ISO week number, so a chosen date drives the seasonal projection."""
    return d.isocalendar()[1]


def weekly_profile(weeks, counts) -> list[float]:
    """53-slot week-of-year count array (index 0 = ISO week 1)."""
    arr = [0.0] * 53
    for w, c in zip(weeks or [], counts or []):
        if w and 1 <= int(w) <= 53:
            arr[int(w) - 1] += float(c)
    return arr


def window(arr: list[float], week: int) -> float:
    """Counts in a 3-week window centred on `week`, wrapping the year."""
    i = (week - 1) % 53
    return arr[(i - 1) % 53] + arr[i] + arr[(i + 1) % 53]


def seasonal_score(profile: list[float], week: int) -> float:
    """How close `week` is to the guild/species peak, in 0..1.

    This is the core of the "what is likely now" ranking and, deliberately,
    takes no weather input: a national test found only a ~10% weather effect
    that did not survive per-guild scrutiny.
    """
    peak = max(window(profile, w) for w in range(1, 54))
    if peak <= 0:
        return 0.0
    return window(profile, week) / peak
