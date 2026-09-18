"""Weather adapter (Open-Meteo, keyless).

Fruiting responds to *antecedent* moisture over days to weeks, so we use
rolling rainfall sums and dry-spell length rather than same-day weather.

KNMI's own open data would be the authoritative Dutch source, but its platform
requires a (free) API key. Open-Meteo is used for the MVP so the system works
without any key; the KNMI adapter can be added later behind the same dict.
"""

from __future__ import annotations

import datetime as dt

import requests

from config import HTTP_TIMEOUT_S, USER_AGENT

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

# De Bilt: representative for the national snapshot. Weather is a 1-2 km
# signal, so it modulates whole areas, never individual 10 m cells.
DEFAULT_LAT = 52.10
DEFAULT_LON = 5.18


def _get(params: dict) -> dict | None:
    try:
        r = requests.get(
            OPEN_METEO, params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT_S,
        )
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        return None


def national_snapshot(lat: float = DEFAULT_LAT, lon: float = DEFAULT_LON) -> dict:
    """Return antecedent-rainfall / temperature / soil state for one point."""
    daily = _get({
        "latitude": lat,
        "longitude": lon,
        "daily": "precipitation_sum,temperature_2m_mean",
        "past_days": 21,
        "forecast_days": 3,
        "timezone": "UTC",
    })
    hourly = _get({
        "latitude": lat,
        "longitude": lon,
        "hourly": "soil_moisture_0_to_7cm,soil_temperature_6cm",
        "past_days": 1,
        "forecast_days": 1,
        "timezone": "UTC",
    })
    if not daily:
        return {}

    times = daily["daily"]["time"]
    precip = daily["daily"]["precipitation_sum"]
    temps = daily["daily"]["temperature_2m_mean"]
    today = dt.date.today().isoformat()

    past = [(t, p) for t, p in zip(times, precip) if t < today and p is not None]
    future = [(t, p) for t, p in zip(times, precip) if t >= today and p is not None]

    def rolling(days: int) -> float:
        return round(sum(p for _, p in past[-days:]), 2)

    dry = 0
    for _, p in reversed(past):
        if p is not None and p >= 1.0:
            break
        dry += 1

    recent_temps = [t for t in temps[-4:-1] if t is not None]
    soil_moisture = None
    soil_temp = None
    if hourly:
        sm = [v for v in hourly["hourly"].get("soil_moisture_0_to_7cm", []) if v is not None]
        st = [v for v in hourly["hourly"].get("soil_temperature_6cm", []) if v is not None]
        if sm:
            soil_moisture = round(sum(sm[-6:]) / len(sm[-6:]), 3)
        if st:
            soil_temp = round(sum(st[-6:]) / len(st[-6:]), 2)

    return {
        "as_of": dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0),
        "precip_7d": rolling(7),
        "precip_14d": rolling(14),
        "precip_21d": rolling(21),
        "dry_days": dry,
        "temp_c": round(sum(recent_temps) / len(recent_temps), 2) if recent_temps else None,
        "soil_temp_c": soil_temp,
        "soil_moisture": soil_moisture,
        "forecast_precip_3d": round(sum(p for _, p in future[:3]), 2),
        "raw": {
            "model": "open-meteo forecast",
            "lat": lat,
            "lon": lon,
            "daily_tail": list(zip(times[-10:], precip[-10:])),
        },
    }


def store_snapshot(conn, scope: str, snap: dict) -> None:
    import json

    if not snap:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO weather_snapshots (scope, as_of, precip_7d, precip_14d,
              precip_21d, dry_days, temp_c, soil_temp_c, soil_moisture,
              forecast_precip_3d, raw)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (scope, as_of) DO NOTHING
            """,
            (scope, snap["as_of"], snap["precip_7d"], snap["precip_14d"],
             snap["precip_21d"], snap["dry_days"], snap["temp_c"],
             snap["soil_temp_c"], snap["soil_moisture"],
             snap["forecast_precip_3d"], json.dumps(snap.get("raw", {}))),
        )
    conn.commit()


def latest_snapshot(conn, scope: str = "nl") -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM weather_snapshots WHERE scope = %s ORDER BY as_of DESC LIMIT 1",
            (scope,),
        )
        return cur.fetchone()
