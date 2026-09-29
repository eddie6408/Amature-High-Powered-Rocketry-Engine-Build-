"""Safety-code checks, certification levels, fin flutter and the flight card."""

import pytest

from aerodyne.app.server import App
from aerodyne.safety import minimum_distances, required_cert_level, safety_review
from aerodyne.structures.flutter import flutter_velocity
from aerodyne.vehicle.components import FinSet
from aerodyne.workspace import Workspace


def test_cert_levels_and_distances():
    assert required_cert_level(150)[0] == 0
    assert required_cert_level(300)[0] == 1 and required_cert_level(640)[0] == 1
    assert required_cert_level(641)[0] == 2 and required_cert_level(5120)[0] == 2
    assert required_cert_level(9000)[0] == 3 and required_cert_level(50000)[0] == 4
    d = minimum_distances(2000)                      # K
    assert d["class"] == "K" and d["personnel_ft"] == 200 and d["personnel_complex_ft"] == 300
    assert minimum_distances(9000)["personnel_m"] == pytest.approx(500 * 0.3048)


def test_safety_review_pass_fail_and_unset():
    base = dict(total_impulse_ns=2000, average_thrust_n=400, liftoff_mass_kg=5.0, rail_elevation_deg=85)
    ok = safety_review(**base, flyer_cert_level=2, wind_mps=4, cloud_cover_pct=20, visibility_m=16000,
                       apogee_agl_m=1500, ceiling_agl_m=3000, landing_energy_j=60, flutter_ratio=2.0)
    assert ok["status"] == "GO" and all(i["status"] in ("PASS", "INFO") for i in ok["items"])
    bad = safety_review(**{**base, "liftoff_mass_kg": 20.0, "rail_elevation_deg": 65}, flyer_cert_level=1,
                        wind_mps=6, gust_mps=11, cloud_cover_pct=80, visibility_m=3000,
                        apogee_agl_m=3500, ceiling_agl_m=3000, flutter_ratio=0.9)
    failed = {i["id"] for i in bad["items"] if i["status"] == "FAIL"}
    assert bad["status"] == "NO-GO"
    assert failed == {"cert", "liftoff_weight", "launch_angle", "wind", "clouds", "visibility", "ceiling", "flutter"}
    unset = safety_review(**base)
    assert unset["status"] == "CHECK" and {"cert", "wind", "clouds", "visibility"} <= {
        i["id"] for i in unset["items"] if i["status"] == "NOT SET"}


def test_flutter_velocity_scaling():
    f = FinSet(name="f", x=0, root_chord=0.15, tip_chord=0.05, span=0.1, thickness=0.003)
    v1 = flutter_velocity(f, 2.9e9, 101325, 340.3)
    assert v1 == pytest.approx(298.7, rel=0.01)                     # hand calculation
    thick = FinSet(name="f", x=0, root_chord=0.15, tip_chord=0.05, span=0.1, thickness=0.006)
    assert flutter_velocity(thick, 2.9e9, 101325, 340.3) == pytest.approx(v1 * 2 ** 1.5, rel=1e-9)   # ~ t^1.5
    assert flutter_velocity(f, 2.9e9, 50000, 320) > v1                # thinner air: higher flutter speed
    with pytest.raises(ValueError):
        flutter_velocity(FinSet(name="f", x=0, thickness=0.0), 2.9e9, 101325, 340.3)


def test_flight_card_and_profile(tmp_path):
    app = App(Workspace.init(tmp_path / "ws", "t"))
    code, prof = app.dispatch("PUT", "/api/profile", {}, {"name": "Pat", "cert_level": "1", "organization": "TRA"})
    assert code == 200 and prof == {"name": "Pat", "cert_level": 1, "organization": "TRA"}
    assert app.dispatch("PUT", "/api/profile", {}, {"cert_level": 7})[0] == 400
    code, card = app.dispatch("POST", "/api/missions/example/flight-card", {},
                              {"conditions": {"wind_speed": 3, "gust_sigma": 0.5, "cloud_cover_pct": 10, "visibility_m": 20000}})
    assert code == 200, card
    assert card["flyer"]["name"] == "Pat" and card["motor"]["class"].startswith("H")
    assert card["prediction"]["apogee_agl_m"] > 100 and card["recovery"] and card["flutter"]
    items = {i["id"]: i for i in card["safety"]["items"]}
    assert items["cert"]["status"] == "PASS" and items["wind"]["status"] == "PASS"
    assert items["ceiling"]["status"] == "NOT SET"                   # example mission has no waiver ceiling
    code, rd = app.dispatch("POST", "/api/missions/example/readiness", {}, {})
    assert any(c["id"].startswith("flutter_") and c["status"] in ("PASS", "WARN", "FAIL") for c in rd["checks"])
