"""OpenRocket pods, parallel stages (side boosters) and tube fins."""

import math
from pathlib import Path

import pytest

from aerodyne.aero.analytical import AnalyticalAeroModel, barrowman_cp
from aerodyne.app.schema import profile
from aerodyne.interop.openrocket import read_ork
from aerodyne.vehicle.components import BodyTube, FinSet, TubeFinSet
from aerodyne.vehicle.mass import MassPropertiesEngine
from aerodyne.vehicle.vehicle import Vehicle

FIX = Path(__file__).parent / "data" / "ork_pods.ork.xml"


def test_import_pods_boosters_tubefins():
    r = read_ork(FIX)
    v = r.vehicle
    c = {x.name: x for x in v.components}
    # main airframe geometry ignores pods and boosters
    assert v.length == pytest.approx(0.8) and v.reference_diameter == pytest.approx(0.06)
    assert v.nose().name == "Nose"
    # tube fins: 'auto' radius makes 6 tubes touch around a 30 mm body
    tf = c["Tube fins"]
    assert isinstance(tf, TubeFinSet) and tf.count == 6
    sn = math.sin(math.pi / 6)
    assert tf.outer_radius == pytest.approx(0.03 * sn / (1 - sn))
    ro, ri = tf.outer_radius, tf.outer_radius - 0.0005
    assert tf.mass == pytest.approx(6 * math.pi * (ro * ro - ri * ri) * 0.08 * 900)
    # pods: two copies, offset from the axis (body radius + pod radius + 10 mm gap)
    pod = c["Pod tube"]
    assert pod.instances == 2 and "pod" in pod.tags and pod.outer_diameter == pytest.approx(0.024)   # 'auto' radius
    assert pod.radial_offset == pytest.approx(0.03 + 0.012 + 0.01)
    assert c["Pod fins"].instances == 2 and c["Pod fins"].body_radius == pytest.approx(0.012)
    assert pod.x + pod.length_ == pytest.approx(0.8)                   # aligned to the bottom of the body
    single = BodyTube("x", x=0, length_=0.2, outer_diameter=0.024, wall_thickness=0.0005, material=pod.material)
    assert pod.mass == pytest.approx(2 * single.mass)
    # boosters: motors recorded with their mount; the airframe's motor slot is the core's
    motors = {m.designation: m for m in r.motors}
    assert motors["F50T"].mount == "main" and motors["D12"].mount == "Boosters" and motors["D12"].count == 2
    assert v.motor_slot.motor_diameter == pytest.approx(0.029)
    assert any("side boosters (stage 1)" in w and "motors & staging" in w for w in r.warnings)
    # stages: the core and its pods are stage 0, the boosters stage 1 with their own motor slot
    assert c["Pod tube"].stage == 0 and c["Booster tube"].stage == 1 and motors["D12"].stage == 1
    assert v.motor_slot_for(1) is not None and v.motor_slot_for(1).stage == 1 and v.motor_slot.stage == 0
    assert any("tube fins" in w for w in r.warnings)


def test_pods_add_lift_drag_and_inertia():
    v = read_ork(FIX).vehicle
    bare = Vehicle(name="bare")
    for x in v.components:
        if "pod" not in x.tags:
            bare.add(x)
    cna_pods, xcp_pods, parts = barrowman_cp(v, 0.3)
    cna_bare, _, _ = barrowman_cp(bare, 0.3)
    assert cna_pods > cna_bare
    assert any(p[0] == "Pod nose" for p in parts)
    cd_pods = AnalyticalAeroModel(v).zero_lift_cd(0.3, 6e6)
    cd_bare = AnalyticalAeroModel(bare).zero_lift_cd(0.3, 6e6)
    assert cd_pods > cd_bare * 1.2
    iyy = MassPropertiesEngine(v).dry().iyy
    assert iyy > MassPropertiesEngine(bare).dry().iyy
    # drawing: pods above and below the axis
    ys = [y for s in profile(v) if s["name"] == "Pod tube" for _, y in s["points"]]
    assert max(ys) > 0.05 and min(ys) < -0.05


def test_custom_materials_survive_the_app(tmp_path):
    """OpenRocket materials that AERODYNE doesn't know travel with the design."""
    import base64

    from aerodyne.app.server import App
    from aerodyne.workspace import Workspace

    app = App(Workspace.init(tmp_path / "ws", "t"))
    code, veh = app.dispatch("POST", "/api/vehicles", {}, {"name": "pods", "template": "ork",
                                                          "ork_b64": base64.b64encode(FIX.read_bytes()).decode()})
    assert code == 200, veh
    code, det = app.dispatch("GET", f"/api/vehicles/{veh['vehicle_id']}", {}, {})
    assert det["design"]["vehicle"]["materials"]["Balsa"]["density"] == 600
    code, an = app.dispatch("POST", "/api/analyze", {}, {"design": det["design"]})
    assert code == 200, an
    assert "Balsa" in app.dispatch("GET", "/api/schema", {}, {})[1]["components"]["FinSet"]["fields"][-2]["options"]


def test_serialisation_keeps_old_designs_unchanged():
    f = FinSet("fins", x=0.5)
    d = Vehicle(name="x")
    d.add(f)
    enc = d.to_dict()["components"][0]
    assert "instances" not in enc and "radial_offset" not in enc and "shear_modulus_gpa" not in enc
    v = read_ork(FIX).vehicle
    again = Vehicle.from_dict(v.to_dict())
    pods = [x for x in again.components if "pod" in x.tags]
    assert pods and all(x.instances == 2 for x in pods)
    assert isinstance(next(x for x in again.components if x.name == "Tube fins"), TubeFinSet)
    assert again.config_hash == v.config_hash
