"""Launch simulator: weather-driven animated flight with a calm-day comparison."""

import pytest

from aerodyne.app.server import App
from aerodyne.workspace import Workspace


def _launch(app, weather, expect=200, **kw):
    code, out = app.dispatch("POST", "/api/missions/example/launch", {}, {"weather": weather, **kw})
    assert code == expect, out
    return out


def test_launch_frames_and_weather_effects(tmp_path):
    app = App(Workspace.init(tmp_path / "ws", "t"))
    calm = _launch(app, {"wind_speed": 0, "temperature_c": 15, "humidity_pct": 0})
    assert calm["kind"] == "SIMULATED" and len(calm["frames"]["t"]) > 100
    fr = calm["frames"]
    assert all(len(fr[k]) == len(fr["t"]) for k in ("east", "north", "up", "thrust", "phase", "ax_u"))
    assert calm["summary"]["apogee_agl_m"] == pytest.approx(calm["calm_summary"]["apogee_agl_m"], rel=0.02)
    assert calm["weather"]["density_ratio"] == pytest.approx(1.0, abs=0.01)
    assert calm["profile"] and calm["length_m"] > 0

    hot = _launch(app, {"wind_speed": 0, "temperature_c": 38, "humidity_pct": 90})
    cold = _launch(app, {"wind_speed": 0, "temperature_c": -10, "humidity_pct": 20})
    assert hot["weather"]["density_ratio"] < 1 < cold["weather"]["density_ratio"]
    assert hot["weather"]["density_altitude_m"] > 500
    assert hot["summary"]["apogee_agl_m"] > cold["summary"]["apogee_agl_m"]   # thin air flies higher

    # wind FROM the west, rail vertical: the vehicle weathercocks west and lands downwind (east)
    windy = _launch(app, {"wind_speed": 8, "wind_from_deg": 270, "rail_elevation_deg": 90})
    assert windy["summary"]["landing_east_m"] > 50
    assert windy["summary"]["apogee_agl_m"] < calm["summary"]["apogee_agl_m"]
    assert windy["frames"]["wind_e"][-1] > 0


def test_launch_pad_and_errors(tmp_path):
    app = App(Workspace.init(tmp_path / "ws", "t"))
    code, pad = app.dispatch("GET", "/api/missions/example/pad", {}, {})
    assert code == 200 and pad["profile"] and pad["length_m"] > 0 and pad["site"]["rail_length"] > 0
    code, out = app.dispatch("POST", "/api/missions/nope/launch", {}, {"weather": {}})
    assert code in (400, 404)


OPEN_METEO_REPLY = {
    "latitude": 40.02, "longitude": -105.27, "elevation": 1655.0, "timezone": "America/Denver",
    "current": {"time": "2026-09-29T14:15", "temperature_2m": 24.3, "relative_humidity_2m": 31,
                "surface_pressure": 836.2, "wind_speed_10m": 4.0, "wind_direction_10m": 250, "wind_gusts_10m": 7.6,
                "cloud_cover": 35, "visibility": 24140.0},
    "hourly": {"time": ["2026-09-29T13:00", "2026-09-29T14:00"],
               "wind_speed_80m": [5.0, 6.5], "wind_direction_80m": [250, 255],
               "wind_speed_120m": [6.0, 7.5], "wind_direction_120m": [255, 260],
               "wind_speed_180m": [7.0, 9.0], "wind_direction_180m": [260, 265]},
}


class _Reply:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def test_live_weather_parse_fetch_and_offline():
    import json
    import urllib.error

    from aerodyne.app.services import BadRequest, live_weather
    from aerodyne.environment.live_weather import LiveWeatherError, fetch_live_weather, open_meteo_url

    seen = []

    def opener(url, timeout):
        seen.append(url)
        return _Reply(json.dumps(OPEN_METEO_REPLY).encode())

    lw = fetch_live_weather(40.0149, -105.2705, opener=opener)
    assert "latitude=40.01490" in seen[0] and "wind_speed_unit=ms" in seen[0] and seen[0] == open_meteo_url(40.0149, -105.2705)
    w = lw["weather"]
    assert w["temperature_c"] == 24.3 and w["humidity_pct"] == 31 and w["pressure_hpa"] == 836.2
    assert w["wind_speed"] == 4.0 and w["wind_from_deg"] == 250
    assert w["gust_sigma"] == pytest.approx((7.6 - 4.0) / 3, abs=0.01)
    assert w["wind_profile"]["altitudes"] == [2.0, 10.0, 80.0, 120.0, 180.0]
    assert w["wind_profile"]["speeds"][2:] == [6.5, 7.5, 9.0]          # the 14:00 hour, not 13:00
    assert lw["kind"] == "ESTIMATED" and lw["elevation_m"] == 1655.0
    assert lw["cloud_cover_pct"] == 35 and lw["visibility_m"] == 24140.0 and "cloud_cover" in seen[0]

    def offline(url, timeout):
        raise urllib.error.URLError("no route to host")

    with pytest.raises(LiveWeatherError, match="not reachable"):
        fetch_live_weather(40.0, -105.0, opener=offline)
    with pytest.raises(BadRequest, match="no internet"):
        live_weather(40.0, -105.0, fetch=lambda la, lo: fetch_live_weather(la, lo, opener=offline))
    with pytest.raises(ValueError):
        fetch_live_weather(95.0, 0.0, opener=opener)


def test_launch_at_location_with_wind_aloft(tmp_path):
    from aerodyne.environment.live_weather import parse_open_meteo

    app = App(Workspace.init(tmp_path / "ws", "t"))
    w = parse_open_meteo(OPEN_METEO_REPLY)["weather"]
    loc = {"latitude": 40.0149, "longitude": -105.2705, "altitude_msl": 1655.0}
    out = _launch(app, w, location=loc)
    assert out["site"]["latitude"] == pytest.approx(40.0149) and out["site"]["altitude_msl"] == 1655.0
    assert out["weather"]["wind_profile"] is True
    assert out["weather"]["pressure_hpa"] == pytest.approx(836.2, abs=0.5)
    assert out["weather"]["density"] < 1.0                              # thin mountain air (sea level ~1.225)
    assert out["weather"]["density_ratio"] < 1.0                        # warm day: thinner than standard at 1655 m
    assert out["weather"]["density_altitude_m"] > 2000
    # wind FROM ~250-265 deg: drift toward the east-north-east, landing point east of the pad
    assert out["landing"]["longitude"] > loc["longitude"]
    # wind aloft (9 m/s at 180 m) is stronger than a power law from 4 m/s would give
    fr = out["frames"]
    i = max(range(len(fr["up"])), key=lambda k: fr["up"][k])
    assert (fr["wind_e"][i] ** 2 + fr["wind_n"][i] ** 2) ** 0.5 == pytest.approx(9.0, abs=0.3)

    _launch(app, w, expect=400, location={"latitude": 123.0, "longitude": 0.0})
    _launch(app, {"wind_profile": {"altitudes": [0], "speeds": [1, 2], "from_deg": [0]}}, expect=400)
