"""Pure helper functions shared by the API and the tests.

Kept free of database and network imports so they can be unit tested without
a running stack.
"""

from __future__ import annotations

import datetime as dt
import re
import time

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





def binomial(scientific_name: str) -> str:
    """Reduce 'Amanita muscaria (L.) Lam.' to 'Amanita muscaria'.

    GBIF stores names with the author citation; Wikipedia article titles and
    most lookups use the bare binomial.
    """
    parts = (scientific_name or "").split()
    return " ".join(parts[:2]) if len(parts) >= 2 else (scientific_name or "").strip()


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


class RateLimiter:
    """Simpele teller per sleutel (IP).

    Eén proces, dus een dict volstaat. Bedoeld om een publieke pagina te
    beschermen tegen iemand die de API leegtrekt, niet als echte quota.
    """

    def __init__(self, limit: int, window_s: float, max_keys: int = 5000) -> None:
        self.limit = limit
        self.window = window_s
        self.max_keys = max_keys
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        recent = [t for t in self._hits.get(key, []) if moment - t < self.window]
        if len(recent) >= self.limit:
            self._hits[key] = recent
            return False
        recent.append(moment)
        self._hits[key] = recent
        if len(self._hits) > self.max_keys:
            for stale in list(self._hits)[: len(self._hits) - self.max_keys]:
                del self._hits[stale]
        return True


class TTLCache:
    """Kleine in-memory cache met vervaltijd, voor dure queries."""

    def __init__(self, ttl_s: float, max_items: int = 1000) -> None:
        self.ttl = ttl_s
        self.max_items = max_items
        self._items: dict[str, tuple[float, object]] = {}

    def get(self, key: str, now: float | None = None):
        moment = time.monotonic() if now is None else now
        hit = self._items.get(key)
        if not hit:
            return None
        stamp, value = hit
        if moment - stamp > self.ttl:
            del self._items[key]
            return None
        return value

    def set(self, key: str, value, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        if len(self._items) >= self.max_items:
            for stale in list(self._items)[: len(self._items) - self.max_items + 1]:
                del self._items[stale]
        self._items[key] = (moment, value)
