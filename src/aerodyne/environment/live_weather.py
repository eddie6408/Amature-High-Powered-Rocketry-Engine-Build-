"""Live weather for a launch location (current conditions from a forecast model).

Uses the Open-Meteo forecast API (no key needed). The values are a numerical
weather model's analysis for the location and hour, not a measurement at the
pad, so they are labelled ESTIMATED. Without internet access the fetch fails
cleanly and weather is entered by hand.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
PROFILE_HEIGHTS = (80, 120, 180)                    # m above ground, from the model's hourly fields


class LiveWeatherError(RuntimeError):
    pass


def open_meteo_url(lat: float, lon: float) -> str:
    current = "temperature_2m,relative_humidity_2m,surface_pressure,wind_speed_10m,wind_direction_10m,wind_gusts_10m"
    hourly = ",".join(f"wind_speed_{h}m,wind_direction_{h}m" for h in PROFILE_HEIGHTS)
    q = {"latitude": f"{lat:.5f}", "longitude": f"{lon:.5f}", "current": current, "hourly": hourly,
         "wind_speed_unit": "ms", "timezone": "auto", "forecast_days": 1}
    return f"{OPEN_METEO_URL}?{urllib.parse.urlencode(q)}"


def check_coordinates(lat: float, lon: float) -> None:
    if not (math.isfinite(lat) and math.isfinite(lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"invalid coordinates {lat}, {lon}: latitude -90..90, longitude -180..180")


def parse_open_meteo(data: dict[str, Any]) -> dict[str, Any]:
    """Open-Meteo response -> launch-simulator weather (SI units, wind FROM direction)."""
    cur = data.get("current") or {}
    need = ("temperature_2m", "relative_humidity_2m", "surface_pressure", "wind_speed_10m", "wind_direction_10m")
    missing = [k for k in need if cur.get(k) is None]
    if missing:
        raise LiveWeatherError(f"weather service reply is missing {', '.join(missing)}")
    speed = float(cur["wind_speed_10m"])
    wfrom = float(cur["wind_direction_10m"]) % 360
    gust = cur.get("wind_gusts_10m")
    # a peak gust is roughly mean + 3 sigma of the turbulent fluctuation
    gust_sigma = max(0.0, (float(gust) - speed) / 3.0) if gust is not None else 0.0

    # wind aloft: the model hour matching the current observation
    alts, speeds, dirs = [2.0, 10.0], [speed * (0.2 ** (1 / 7)), speed], [wfrom, wfrom]
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    hour = str(cur.get("time", ""))[:13]
    idx = next((i for i, t in enumerate(times) if str(t)[:13] == hour), None)
    if idx is not None:
        for h in PROFILE_HEIGHTS:
            s = (hourly.get(f"wind_speed_{h}m") or [None] * len(times))[idx]
            d = (hourly.get(f"wind_direction_{h}m") or [None] * len(times))[idx]
            if s is not None and d is not None:
                alts.append(float(h))
                speeds.append(float(s))
                dirs.append(float(d) % 360)
    weather: dict[str, Any] = {
        "wind_speed": round(speed, 2), "wind_from_deg": round(wfrom), "gust_sigma": round(gust_sigma, 2),
        "temperature_c": round(float(cur["temperature_2m"]), 1),
        "humidity_pct": round(float(cur["relative_humidity_2m"])),
        "pressure_hpa": round(float(cur["surface_pressure"]), 1),
    }
    if len(alts) > 2:
        weather["wind_profile"] = {"altitudes": alts, "speeds": [round(s, 2) for s in speeds],
                                   "from_deg": [round(d) for d in dirs]}
    return {
        "weather": weather,
        "latitude": data.get("latitude"), "longitude": data.get("longitude"),
        "elevation_m": data.get("elevation"),
        "observed_at": cur.get("time"), "timezone": data.get("timezone"),
        "peak_gust": gust,
        "source": "Open-Meteo forecast model (current conditions)",
        "kind": "ESTIMATED",
        "note": "Model analysis for this location and hour, not a measurement at the pad. "
                "Check against a pad anemometer and thermometer on launch day.",
    }


def fetch_live_weather(lat: float, lon: float, timeout: float = 10.0,
                       opener: Callable[..., Any] = urllib.request.urlopen) -> dict[str, Any]:
    check_coordinates(lat, lon)
    try:
        with opener(open_meteo_url(lat, lon), timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise LiveWeatherError(f"weather service not reachable - no internet? ({reason})") from exc
    except json.JSONDecodeError as exc:
        raise LiveWeatherError("weather service sent an unreadable reply") from exc
    if isinstance(data, dict) and data.get("error"):
        raise LiveWeatherError(f"weather service: {data.get('reason', 'error')}")
    return parse_open_meteo(data)
