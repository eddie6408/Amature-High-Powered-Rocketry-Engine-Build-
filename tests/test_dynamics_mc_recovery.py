import math
from dataclasses import replace

import numpy as np
import pytest

from aerodyne.aero.model import ScaledAeroModel
from aerodyne.dynamics.simulator import FlightSimulator, LaunchSite
from aerodyne.environment import ConstantWind, StandardAtmosphere
from aerodyne.examples import example_config, example_recovery
from aerodyne.montecarlo import MonteCarloEngine, Normal, UncertaintyModel
from aerodyne.recovery import analyze_recovery, descent_rate
from aerodyne.structures import derive_load_cases, margin_of_safety


def test_nominal_flight_is_sane(nominal_sim):
    s = nominal_sim.summary()
    assert s["kind"] == "SIMULATED"
    assert 600 < s["apogee_agl_m"] < 1100
    assert s["rail_exit_velocity_mps"] > 15
    assert s["min_stability_margin_cal"] > 1.0
    names = [n for _, n in nominal_sim.events]
    assert names[:4] == ["liftoff", "rail_exit", "burnout", "apogee"]
    assert "landing" in names and "deploy:main" in names
    assert nominal_sim.phase[-1] == "landed"


def test_energy_bound_vacuum_like():
    # apogee cannot exceed the drag-free ballistic height from burnout
    cfg = example_config(wind=ConstantWind())
    s = FlightSimulator(cfg).run().summary()
    v, h = s["burnout_velocity_mps"], s["burnout_altitude_agl_m"]
    assert s["apogee_agl_m"] < h + v * v / (2 * 9.80665)


def test_more_drag_lowers_apogee():
    base = example_config(stop_at_apogee=True, dt=0.01)
    a0 = FlightSimulator(base).run().summary()["apogee_agl_m"]
    a1 = FlightSimulator(replace(base, aero=ScaledAeroModel(base.aero, cd_scale=1.3))).run().summary()["apogee_agl_m"]
    assert a1 < a0


def test_weathercocking_into_wind():
    cfg = example_config(wind=ConstantWind(8.0, 270.0), stop_at_apogee=True, dt=0.01,
                         site=LaunchSite(rail_length=1.8, elevation_deg=90.0))
    res = FlightSimulator(cfg).run()
    # wind from the west: a stable rocket turns into the wind, apogee lies west of the pad
    assert res.position[-1, 0] < 0


def test_attitude_quaternion_stays_normalized(nominal_sim):
    n = np.linalg.norm(nominal_sim.attitude, axis=1)
    assert np.allclose(n, 1.0, atol=1e-9)


def test_descent_rate_formula():
    assert descent_rate(10.0, 2.0, 1.225) == pytest.approx(math.sqrt(2 * 10 * 9.80665 / (1.225 * 2)))


def test_recovery_analysis_independent_of_propulsion():
    est = analyze_recovery(1.4, example_recovery(), apogee_agl=800, site_altitude=100,
                           atmosphere=StandardAtmosphere(), wind=ConstantWind(5, 270))
    assert est.descent_rates["main"] < est.descent_rates["drogue"]
    assert est.drift[0] > 0              # drifts downwind (east)
    assert est.timeline[-1][1] == "landing"
    assert all(v > 0 for v in est.opening_loads.values())


def test_monte_carlo_distribution_small():
    unc = UncertaintyModel(wind_speed=Normal(3, 1))
    mc = MonteCarloEngine(example_config(), unc, dt=0.02).run(6, seed=3)
    assert len(mc.outputs) == 6 and not mc.failures
    st = mc.statistics("apogee_agl_m")
    assert st["std"] > 0 and st["p05"] <= st["p50"] <= st["p95"]
    assert mc.landing_dispersion()["semi_major_m"] >= mc.landing_dispersion()["semi_minor_m"]
    mc2 = MonteCarloEngine(example_config(), unc, dt=0.02).run(6, seed=3)
    assert mc2.values("apogee_agl_m") == pytest.approx(mc.values("apogee_agl_m"))


def test_structural_cases(nominal_sim):
    cases = derive_load_cases(nominal_sim, example_config().vehicle, {"main": 400.0}, 5.0)
    types = {c.load_type for c in cases}
    assert types == {"acceleration", "aerodynamic", "recovery", "landing"}
    assert margin_of_safety(300e6, 100e6, 1.5) == pytest.approx(1.0)
    assert margin_of_safety(100e6, 100e6, 1.5) < 0
