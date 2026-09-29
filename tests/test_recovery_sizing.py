"""Parachute sizing, standard sizes, landing energy per section, drift table."""

import math

import pytest

from aerodyne.app.server import App
from aerodyne.recovery.sizing import IN, diameter_for, drift_table, rate_for, size_canopy
from aerodyne.recovery.recovery import RecoveryConfig, RecoveryDevice
from aerodyne.workspace import Workspace


def test_diameter_and_rate_are_inverse():
    d = diameter_for(1.4, 5.5, 1.5, 1.2)
    assert d == pytest.approx(math.sqrt(4 * 2 * 1.4 * 9.80665 / (1.2 * 5.5 ** 2) / (math.pi * 1.5)))
    assert rate_for(1.4, d, 1.5, 1.2) == pytest.approx(5.5)
    assert diameter_for(1.4, 5.5, 1.5, 1.2, extra_cd_area=0.05) < d          # body drag helps
    with pytest.raises(ValueError):
        diameter_for(1.4, 0, 1.5, 1.2)


def test_size_canopy_recommends_next_standard_size():
    out = size_canopy(1.4, 5.5, 1.5, altitude_msl=1600, sections_kg=[0.6, 0.8])
    assert out["density"] < 1.1                                               # thinner air at 1600 m
    rec = [o for o in out["options"] if o["recommended"]]
    assert len(rec) == 1 and rec[0]["diameter_in"] * IN >= out["required_diameter_m"]
    smaller = [o for o in out["options"] if o["diameter_in"] * IN < out["required_diameter_m"]]
    assert smaller and smaller[0]["rate_mps"] > 5.5 > rec[0]["rate_mps"]
    e = rec[0]["section_energy_ftlbf"]
    assert e[1] > e[0] and rec[0]["max_section_energy_ftlbf"] == pytest.approx(e[1])


def test_drift_grows_with_wind_and_main_altitude():
    cfg = RecoveryConfig(devices=(RecoveryDevice("drogue", 1.5, 0.3), RecoveryDevice("main", 2.2, 0.9, "altitude", 150.0)))
    t = drift_table(1.2, cfg, 900.0, 0.0, winds=[0.0, 5.0, 8.0], main_altitudes=[150.0, 400.0])
    low, high = t["rows"]
    assert low["cells"][0]["drift_m"] == pytest.approx(0.0, abs=1.0)
    assert low["cells"][1]["drift_m"] < low["cells"][2]["drift_m"]
    assert high["cells"][1]["drift_m"] > low["cells"][1]["drift_m"]            # longer under the main
    assert high["cells"][1]["descent_s"] > low["cells"][1]["descent_s"]


def test_recovery_api(tmp_path):
    app = App(Workspace.init(tmp_path / "ws", "t"))
    code, out = app.dispatch("POST", "/api/recovery/size", {}, {"mass_kg": 1.4, "target_rate_mps": 5.5, "cd": 2.2})
    assert code == 200 and out["canopy_cd"]["toroidal"] == 2.2
    assert app.dispatch("POST", "/api/recovery/size", {}, {"mass_kg": 1.4})[0] == 400
    code, d = app.dispatch("POST", "/api/missions/example/recovery-drift", {}, {"winds": [0, 5], "main_altitudes": [150, 300]})
    assert code == 200 and d["has_main"] and len(d["rows"]) == 2 and d["apogee_agl_m"] > 100
