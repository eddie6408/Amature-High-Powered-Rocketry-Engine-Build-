"""Clusters, airstarts, stacked two-stage flights and side boosters."""

import numpy as np
import pytest

from aerodyne.aero.analytical import AnalyticalAeroModel
from aerodyne.dynamics.propulsion_system import MotorGroup, PropulsionSystem, StageSeparation
from aerodyne.dynamics.simulator import FlightSimulator, LaunchSite, SimulationConfig
from aerodyne.propulsion.motor import synthetic_motor
from aerodyne.vehicle.components import BodyTube, FinSet, MotorSlot, NoseCone
from aerodyne.vehicle.vehicle import Vehicle

H = synthetic_motor("SYN-H", 300.0, 1.5, 0.35, 0.18)
G = synthetic_motor("SYN-G", 120.0, 1.2, 0.15, 0.07)
SITE = LaunchSite(rail_length=2.0, elevation_deg=90.0)


def single_stage() -> Vehicle:
    v = Vehicle(name="core")
    v.add(NoseCone("nose", x=0.0, length_=0.3, diameter=0.066, wall_thickness=0.002))
    v.add(BodyTube("body", x=0.3, length_=0.9, outer_diameter=0.066, wall_thickness=0.0015))
    v.add(FinSet("fins", x=1.02, count=3, root_chord=0.15, tip_chord=0.06, span=0.08, sweep=0.08, thickness=0.003, body_radius=0.033))
    v.add(MotorSlot("motor", x=0.95, motor_length=0.25, motor_diameter=0.038))
    return v


def two_stage() -> Vehicle:
    v = Vehicle(name="two-stage")
    v.add(NoseCone("nose", x=0.0, length_=0.3, diameter=0.054, wall_thickness=0.002))
    v.add(BodyTube("sustainer tube", x=0.3, length_=0.6, outer_diameter=0.054, wall_thickness=0.0015))
    v.add(FinSet("sustainer fins", x=0.75, count=3, root_chord=0.12, tip_chord=0.05, span=0.06, sweep=0.06, thickness=0.003, body_radius=0.027))
    v.add(MotorSlot("sustainer motor", x=0.64, motor_length=0.25, motor_diameter=0.038))
    v.add(BodyTube("booster tube", x=0.9, length_=0.5, outer_diameter=0.054, wall_thickness=0.0015, stage=1))
    v.add(FinSet("booster fins", x=1.25, count=4, root_chord=0.15, tip_chord=0.07, span=0.08, sweep=0.08, thickness=0.003,
                 body_radius=0.027, stage=1))
    v.add(MotorSlot("booster motor", x=1.15, motor_length=0.25, motor_diameter=0.038, stage=1))
    return v


def run(v, motor, prop=None, **kw):
    cfg = SimulationConfig(vehicle=v, motor=motor, aero=AnalyticalAeroModel(v), site=SITE, propulsion=prop, dt=0.005, **kw)
    return FlightSimulator(cfg).run()


def test_simple_propulsion_is_identical_to_a_single_motor():
    v = single_stage()
    a = run(v, H, stop_at_apogee=True)
    b = run(v, G, PropulsionSystem((MotorGroup(H),)), stop_at_apogee=True)     # the system's motor wins
    assert np.array_equal(a.position, b.position)


def test_cluster_adds_impulse_and_mass():
    v = single_stage()
    one = run(v, H, stop_at_apogee=True)
    two = run(v, H, PropulsionSystem((MotorGroup(H, count=2),)), stop_at_apogee=True)
    assert two.mass[0] == pytest.approx(one.mass[0] + H.total_mass)
    assert two.thrust.max() == pytest.approx(2 * one.thrust.max(), rel=0.02)
    assert two.summary()["apogee_agl_m"] > one.summary()["apogee_agl_m"] * 1.4
    assert PropulsionSystem((MotorGroup(H, count=2),)).total_impulse == pytest.approx(600, rel=1e-6)


def test_airstart_lights_later():
    v = single_stage()
    prop = PropulsionSystem((MotorGroup(H), MotorGroup(G, count=2, ignition="time", delay=2.0)))
    r = run(v, H, prop, stop_at_apogee=True)
    ign = [t for t, e in r.events if e == "ignition:0"]
    assert ign and ign[0] == pytest.approx(2.0, abs=0.01)
    i = np.searchsorted(r.t, 2.3)
    assert r.thrust[i] > 0 and r.thrust[np.searchsorted(r.t, 1.8)] == 0     # coast gap, then the airstart
    burn = r.event_time("burnout")
    assert burn == pytest.approx(2.0 + G.burn_time, abs=0.01)
    assert r.summary()["apogee_agl_m"] > run(v, H, stop_at_apogee=True).summary()["apogee_agl_m"]


def test_two_stage_separation_and_sustainer_ignition():
    v = two_stage()
    prop = PropulsionSystem((MotorGroup(H, stage=1), MotorGroup(G, stage=0, ignition="separation", delay=0.5)),
                            (StageSeparation(1, delay=0.2),))
    r = run(v, H, prop)
    ev = dict((e, t) for t, e in r.events)
    assert ev["burnout:1"] == pytest.approx(H.burn_time, abs=0.01)
    assert ev["separation:1"] == pytest.approx(H.burn_time + 0.2, abs=0.01)
    assert ev["ignition:0"] == pytest.approx(ev["separation:1"] + 0.5, abs=0.01)
    assert ev["burnout"] == pytest.approx(ev["ignition:0"] + G.burn_time, abs=0.01)
    k = np.searchsorted(r.t, ev["separation:1"])
    assert r.mass[k + 2] < r.mass[k - 2] - 0.3                       # booster (tube, fins, spent motor) gone
    assert r.xcp[k + 2] < r.xcp[k - 2]                                # CP moves forward with the booster fins gone
    booster = r.bodies[0]
    assert booster["stage"] == 1 and not booster["parallel"]
    assert booster["apogee_agl_m"] < r.summary()["apogee_agl_m"]
    assert booster["landing_time_s"] > booster["separation_time_s"]
    # the sustainer flies higher than the whole stack on the booster motor alone
    stack_only = run(v, H, stop_at_apogee=True)
    assert r.summary()["apogee_agl_m"] > stack_only.summary()["apogee_agl_m"]
    assert any("stage 1" in n for n in r.notes)


def test_side_boosters_separate():
    v = single_stage()
    v.add(NoseCone("booster nose", x=0.7, length_=0.1, diameter=0.03, wall_thickness=0.001, stage=1, instances=2, radial_offset=0.05,
                   tags={"pod", "pod:boosters"}))
    v.add(BodyTube("booster tube", x=0.8, length_=0.4, outer_diameter=0.03, wall_thickness=0.001, stage=1, instances=2,
                   radial_offset=0.05, tags={"pod", "pod:boosters"}))
    v.add(MotorSlot("booster motor", x=0.95, motor_length=0.25, motor_diameter=0.029, stage=1, instances=2, radial_offset=0.05,
                    tags={"pod", "pod:boosters"}))
    prop = PropulsionSystem((MotorGroup(H), MotorGroup(G, count=2, stage=1)), (StageSeparation(1, delay=0.1, parallel=True),))
    r = run(v, H, prop)
    assert any(e == "separation:1" for _, e in r.events)
    assert r.bodies[0]["parallel"] and r.bodies[0]["stage"] == 1
    assert r.thrust.max() > H.peak_thrust * 1.3                        # core + two boosters at liftoff


def test_validation_and_monte_carlo():
    with pytest.raises(ValueError):
        PropulsionSystem((MotorGroup(G, ignition="separation"),))       # nothing below to separate
    with pytest.raises(ValueError):
        PropulsionSystem((MotorGroup(H),), (StageSeparation(0),))
    with pytest.raises(ValueError):
        MotorGroup(H, count=0)
    from aerodyne.montecarlo import MonteCarloEngine, UncertaintyModel

    v = two_stage()
    prop = PropulsionSystem((MotorGroup(H, stage=1), MotorGroup(G, stage=0, ignition="separation", delay=0.5)),
                            (StageSeparation(1, delay=0.2),))
    cfg = SimulationConfig(vehicle=v, motor=H, aero=AnalyticalAeroModel(v), site=SITE, propulsion=prop, stop_at_apogee=True)
    mc = MonteCarloEngine(cfg, UncertaintyModel(), dt=0.01).run(4, seed=3)
    apos = mc.values("apogee_agl_m")
    assert len(apos) == 4 and np.ptp(apos) > 0
    assert prop.total_impulse == pytest.approx(420, rel=1e-6) and prop.liftoff_average_thrust == pytest.approx(H.average_thrust)


def test_staged_mission_through_the_app(tmp_path):
    from aerodyne.app import services as svc
    from aerodyne.app.server import App
    from aerodyne.recovery.recovery import RecoveryConfig, RecoveryDevice
    from aerodyne.workspace import Workspace
    from aerodyne.workspace.design import Design

    ws = Workspace.init(tmp_path / "ws", "t")
    app = App(ws)
    rec = RecoveryConfig(devices=(RecoveryDevice("main", 1.5, 0.6),))
    vid = ws.create_vehicle("Two stage", Design(two_stage(), rec))["vehicle_id"]
    kh, kg = ws.add_motor(H), ws.add_motor(G)
    m = app.dispatch("GET", "/api/missions/example", {}, {})[1]
    m.update(id="two", name="two stage", vehicle_id=vid, revision="REV-A", motor_key=kh,
             motors=[{"motor_key": kh, "count": 1, "stage": 1, "ignition": "launch"},
                     {"motor_key": kg, "count": 1, "stage": 0, "ignition": "separation", "delay": 0.5}],
             separations=[{"stage": 1, "delay": 0.2}])
    assert app.dispatch("POST", "/api/missions", {}, m)[0] == 200
    code, sim = app.dispatch("POST", "/api/missions/two/simulate", {}, {})
    assert code == 200, sim
    code, card = app.dispatch("POST", "/api/missions/two/flight-card", {}, {"conditions": {}})
    assert code == 200, card
    assert card["motor"]["total_impulse_ns"] == pytest.approx(420, rel=1e-3) and card["motor"]["motors"] == 2
    assert card["stages"] and card["stages"][0]["stage"] == 1
    liftoff = next(i for i in card["safety"]["items"] if i["id"] == "liftoff_weight")
    assert f"{H.average_thrust:.0f} N" in liftoff["value"]                 # only the booster motor lifts off
    code, rd = app.dispatch("POST", "/api/missions/two/readiness", {}, {})
    assert code == 200 and "SYN-H" in next(c for c in rd["checks"] if c["id"] == "motor_data")["value"]
    code, launch = app.dispatch("POST", "/api/missions/two/launch", {}, {"weather": {"wind_speed": 2}})
    assert code == 200 and launch["stages"] and any(e[1] == "separation:1" for e in launch["events"])
    mc = svc.monte_carlo(ws, "two", 3)
    assert mc["summary"]["runs"] == 3
    # changing a motor in the list makes earlier results stale
    h1 = svc.mission_hashes(ws, ws.mission("two"))["motor"]
    m["motors"][1]["motor_key"] = kh
    app.dispatch("POST", "/api/missions", {}, m)
    assert svc.mission_hashes(ws, ws.mission("two"))["motor"] != h1
    bad = {**m, "motors": [{"motor_key": kh, "stage": 0, "ignition": "separation"}], "separations": []}
    app.dispatch("POST", "/api/missions", {}, bad)
    assert app.dispatch("POST", "/api/missions/two/simulate", {}, {})[0] == 400
