"""Species photos from Wikipedia / Wikimedia Commons.

Deliberately not Observation.org or iNaturalist images: those are licensed
CC BY-NC-ND and may not be redistributed. Wikipedia lead images are freely
licensed, and each is linked back to its Commons file page so the exact
credit and licence stay available.

One thumbnail URL is cached per species; the UI never calls out at runtime.
"""

from __future__ import annotations

import time
from typing import Callable

import requests

from config import HTTP_TIMEOUT_S, USER_AGENT

WIKI_API = "https://en.wikipedia.org/w/api.php"
COMMONS_FILE = "https://commons.wikimedia.org/wiki/File:{}"


def _fetch(scientific_name: str) -> dict | None:
    params = {
        "action": "query",
        "format": "json",
        "prop": "pageimages",
        "piprop": "thumbnail|name",
        "pithumbsize": 360,
        "redirects": 1,
        "titles": scientific_name,
    }
    for attempt in range(3):
        try:
            r = requests.get(WIKI_API, params=params,
                             headers={"User-Agent": USER_AGENT},
                             timeout=HTTP_TIMEOUT_S)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            pages = (r.json().get("query") or {}).get("pages") or {}
            for page in pages.values():
                thumb = (page.get("thumbnail") or {}).get("source")
                if thumb and page.get("pageimage"):
                    return {
                        "image_url": thumb,
                        "image_credit": "Wikimedia Commons",
                        "image_credit_url": COMMONS_FILE.format(page["pageimage"]),
                    }
            return None
        except (requests.RequestException, ValueError):
            if attempt == 2:
                return None
            time.sleep(2 * (attempt + 1))
    return None


def enrich_species_images(conn, progress: Callable[[float, str, str], None] | None = None) -> int:
    """Fill in image_url for species that do not have one yet."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, scientific_name FROM species "
            "WHERE enabled AND image_url IS NULL ORDER BY scientific_name"
        )
        todo = cur.fetchall()

    done = 0
    for i, row in enumerate(todo):
        found = _fetch(row["scientific_name"])
        if found:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE species SET image_url = %s, image_credit = %s, "
                    "image_credit_url = %s WHERE id = %s",
                    (found["image_url"], found["image_credit"],
                     found["image_credit_url"], row["id"]),
                )
            done += 1
        if progress and todo:
            progress(0.05 + 0.08 * (i + 1) / len(todo), "images",
                     "photos {}/{}".format(i + 1, len(todo)))
        time.sleep(0.4)  # be polite to the Wikimedia API

    conn.commit()
    return done
