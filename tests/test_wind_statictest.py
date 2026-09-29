"""Measured wind profiles in missions/Monte Carlo and static-test characterization."""

import numpy as np
import pytest

from aerodyne.app.server import App
from aerodyne.environment.wind_import import parse_wind_profile
from aerodyne.propulsion.motor import synthetic_motor
from aerodyne.workspace import Workspace


def _call(app, m, p, b=None, expect=200):
    code, out = app.dispatch(m, p, {}, b or {})
    assert code == expect, out
    return out


def test_parse_forecast_and_sounding():
    w = parse_wind_profile("Altitude (ft),Speed (kt),Direction\n0,5,270\n1000,9,280\n3000,15,290\n")
    assert w["altitudes"] == [0.0, 304.8, 914.4] and w["speeds"][0] == pytest.approx(2.57, abs=0.01)
    s = parse_wind_profile("HGHT SKNT DRCT\n100 4 250\n600 10 260\n", "MSL", 100)
    assert s["altitudes"] == [0.0, 500.0] and s["from_deg"] == [250.0, 260.0]
    with pytest.raises(ValueError):
        parse_wind_profile("a,b\n1,2\n")


def test_layered_wind_mission_and_monte_carlo(tmp_path):
    ws = Workspace.init(tmp_path / "ws", "t")
    app = App(ws)
    m = _call(app, "GET", "/api/missions/example")
    w = _call(app, "POST", "/api/wind/parse", {"text": "alt (m),speed (m/s),dir\n0,2,90\n500,8,90\n1500,15,90\n"})
    m["wind"] = {k: w[k] for k in ("model", "altitudes", "speeds", "from_deg")}
    _call(app, "POST", "/api/missions", m)
    sim = _call(app, "POST", "/api/missions/example/simulate")
    # wind FROM the east (90 deg): the vehicle drifts west
    assert sim["summary"]["landing_east_m"] < -100
    unc = ws.mission("example").uncertainty_model()
    assert unc.wind_factory is not None
    wind = unc.wind_factory(2.0, 0.0)
    assert wind.at(500, 0)[0] == pytest.approx(-16.0)          # speeds scaled, direction kept
    from aerodyne.app.services import build_config
    from aerodyne.montecarlo import MonteCarloEngine

    cfg, _ = build_config(ws, ws.mission("example"))
    mc = MonteCarloEngine(cfg, unc, dt=0.02).run(5, seed=2)
    east = mc.values("landing_east_m")
    assert len(east) == 5 and np.all(east < 0)                    # every dispersed run drifts west
    assert {"wind_scale", "wind_dir_offset"} <= set(mc.samples[0])


def test_static_test_characterization_and_save(tmp_path):
    ws = Workspace.init(tmp_path / "ws", "t")
    app = App(ws)
    ref = synthetic_motor()
    rng = np.random.default_rng(3)
    t = np.arange(-0.5, 2.5, 0.001)
    lbf = (np.array([ref.thrust_at(x) for x in t]) + 4.0 + rng.normal(0, 0.8, t.size)) / 4.4482216
    text = "Time (ms),Load (lbf)\n" + "".join(f"{int(round((a + 0.5) * 1000))},{b:.4f}\n" for a, b in zip(t, lbf))
    body = {"text": text, "time_col": "Time (ms)", "force_col": "Load (lbf)", "time_unit": "ms", "force_unit": "lbf"}
    r = _call(app, "POST", "/api/motors/static-test", body)
    s = r["summary"]
    assert s["total_impulse_Ns"] == pytest.approx(ref.total_impulse, rel=0.01)
    assert s["baseline_N"] == pytest.approx(4.0, abs=0.3) and s["kind"] == "DERIVED" and r["saved_key"] is None
    _call(app, "POST", "/api/motors/static-test", {**body, "save": True}, expect=400)   # metadata required
    saved = _call(app, "POST", "/api/motors/static-test", {**body, "save": True, "manufacturer": "Acme",
                                                           "designation": "H300-TEST", "total_mass": 0.35,
                                                           "propellant_mass": 0.18, "source_date": "2026-09-01"})
    m = ws.motor(saved["saved_key"])
    assert m.metadata.data_quality.value == "MEASURED" and r["sha256"][:16] in m.metadata.source
    assert m.thrust_uncertainty_rel > 0
