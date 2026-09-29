"""STEP import, real-file OpenRocket features, cross-validation and the calibrated
twin loop (spec 49)."""

import base64
import math
import time
from pathlib import Path

import pytest

from aerodyne.app.server import App
from aerodyne.cad import step as step_mod
from aerodyne.interop import read_ork
from aerodyne.interop.ork_validate import render, validate
from aerodyne.vehicle.components import FinSet, PointMass
from aerodyne.vehicle.mass import MassPropertiesEngine
from aerodyne.workspace import Workspace

DATA = Path(__file__).parent / "data"
needs_gmsh = pytest.mark.skipif(not step_mod.available(), reason="gmsh (OpenCASCADE) not installed")


def _step_file(tmp_path):
    import gmsh

    f = tmp_path / "parts.step"
    gmsh.initialize(["-noenv"])
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("t")
    o = gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, 400, 33)
    i = gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, 400, 31.5)
    gmsh.model.occ.cut([(3, o)], [(3, i)])
    gmsh.model.occ.addBox(-10, -10, 500, 20, 20, 50)
    gmsh.model.occ.synchronize()
    gmsh.write(str(f))
    gmsh.finalize()
    return f


@needs_gmsh
def test_step_exact_mass_properties(tmp_path):
    f = _step_file(tmp_path)
    tube, box = step_mod.step_parts(f, 1850.0, "+z", 0.3, name="asm")
    m = math.pi * (0.033 ** 2 - 0.0315 ** 2) * 0.4 * 1850
    assert tube.mass == pytest.approx(m, rel=1e-6) and tube.cg_station == pytest.approx(0.5, abs=1e-9)
    iyy = m * (3 * (0.033 ** 2 + 0.0315 ** 2) + 0.4 ** 2) / 12
    assert tube.iyy == pytest.approx(iyy, rel=1e-6)
    assert box.mass == pytest.approx(20e-6 * 1850, rel=1e-6) and box.cg_station == pytest.approx(0.825)
    assert "translator" not in tube.name
    merged = step_mod.step_parts(f, 1850.0, "+z", 0.3, merge=True)[0]
    assert merged.mass == pytest.approx(tube.mass + box.mass)
    assert merged.cg_station == pytest.approx((tube.mass * 0.5 + box.mass * 0.825) / merged.mass)


@needs_gmsh
def test_cad_upload_api(tmp_path):
    app = App(Workspace.init(tmp_path / "ws", "t"))
    b64 = base64.b64encode(_step_file(tmp_path).read_bytes()).decode()
    code, out = app.dispatch("POST", "/api/cad/part", {}, {"name": "sled", "filename": "sled.step", "b64": b64,
                                                          "density": 1240, "axis": "+z", "nose_station": 0.5})
    assert code == 200 and len(out["components"]) == 1
    c = out["components"][0]
    assert c["type"] == "CadPart" and c["kind"] == "ESTIMATED" and len(c["source_sha256"]) == 64
    code, out = app.dispatch("POST", "/api/cad/part", {}, {"name": "x", "filename": "x.iges", "b64": b64, "density": 1})
    assert code == 400


def test_openrocket_real_file_features():
    r = read_ork(DATA / "ork_features.ork.xml")
    v = r.vehicle
    comps = {c.name: c for c in v.components}
    # weighed stage: subtree scaled to the weighed total, only the total measured
    assert MassPropertiesEngine(v).dry().mass == pytest.approx(1.0, rel=1e-9)
    assert any("weighed total 1.0000 kg" in w for w in r.warnings)
    # solid nose and its capped shoulder
    assert "Nose aft shoulder" in comps and comps["Nose"].wall_thickness == pytest.approx(0.03)
    # freeform fins -> equal-area trapezoid; elliptical canards likewise; both flagged ESTIMATED
    ff = comps["Freeform"]
    assert isinstance(ff, FinSet) and ff.root_chord == pytest.approx(0.14) and ff.span == pytest.approx(0.07)
    assert 0.5 * (ff.root_chord + ff.tip_chord) * ff.span == pytest.approx(0.5 * (0.14 + 0.06) * 0.07)
    assert sum("approximated by an equal-area trapezoid" in w for w in r.warnings) == 2
    assert "Freeform tabs/fillets" in comps
    # auto-radius ring fits between the airframe and the motor mount; engine block and lug have mass
    assert isinstance(comps["Ring"], PointMass) and comps["Ring"].mass_estimate > 0
    ring = comps["Ring"]
    expected_ring = math.pi * ((0.03 - 0.0015) ** 2 - 0.0145 ** 2) * 0.006 * 630
    assert ring.mass_estimate == pytest.approx(expected_ring, rel=1e-6)
    assert comps["Block"].mass_estimate > 0 and comps["Lug"].mass_estimate > 0
    assert not [w for w in r.warnings if "unsupported" in w]


def test_cross_validation_against_stored_openrocket_data(tmp_path):
    res = validate(DATA / "ork_features.ork.xml", mach=0.3)
    rows = {r["quantity"].split(" (")[0]: r for r in res["rows"]}
    assert res["simulation"] == "Sim A"
    assert rows["Dry mass"]["openrocket"] == pytest.approx(1.0) and rows["Dry mass"]["relative"] == pytest.approx(0, abs=1e-9)
    assert rows["CP at Mach 0.3"]["openrocket"] == pytest.approx(0.84)      # boost sample nearest Mach 0.3
    assert "OpenRocket cross-validation" in render(res)
    plain = validate(DATA / "dual_deploy.ork.xml")
    assert plain["rows"] == [] and "no stored OpenRocket simulation data" in plain["note"]


def _wait(app, job):
    for _ in range(600):
        code, j = app.dispatch("GET", f"/api/jobs/{job}", {}, {})
        if j["status"] != "running":
            return j
        time.sleep(0.2)


def test_calibrated_twin_loop(tmp_path):
    """Spec 49: after a flight, the adopted calibration becomes a new twin revision that
    later simulations use; the flown revision stays locked."""
    from aerodyne.dynamics.simulator import FlightSimulator
    from aerodyne.examples import example_config
    from aerodyne.aero.model import ScaledAeroModel
    from dataclasses import replace
    from aerodyne.sil.runner import SILRunner

    ws = Workspace.init(tmp_path / "ws", "t")
    app = App(ws)
    m = ws.list_missions()[0]
    call = lambda meth, p, b=None: app.dispatch(meth, p, {}, b or {})
    before = call("POST", f"/api/missions/{m.id}/simulate")[1]["summary"]["apogee_agl_m"]
    ws.create_flight("F-1", m.vehicle_id, "REV-A", m.motor_key, "2026-10-01", mission_id=m.id)
    base = example_config()
    truth = FlightSimulator(replace(base, aero=ScaledAeroModel(base.aero, cd_scale=1.15))).run()
    frames = SILRunner(truth, []).run().radio_frames
    from aerodyne.ground.server import Broadcaster
    cap = tmp_path / "t.cap"
    b = Broadcaster(capture=cap)
    for t, d in frames:
        b.publish(d, t)
    b.close()
    ws.add_raw_file("F-1", "flight.cap", cap.read_bytes())
    code, an = call("POST", "/api/flights/F-1/analyze", {"file": "flight.cap"})
    assert code == 200 and an["status"] == "DEVIATION"
    code, prop = call("POST", "/api/flights/F-1/calibrate")
    k = prop["proposal"]["value"]
    assert k == pytest.approx(1.15, abs=0.06)
    code, out = call("POST", "/api/flights/F-1/adopt", {"cd_scale": k})
    assert code == 200 and out["revision"] == "REV-B"
    assert ws.vehicle_summary(m.vehicle_id)["revisions"][0]["status"] == "FLOWN"
    cal = ws.design(m.vehicle_id, "REV-B").calibration
    assert cal["source_flight"] == "F-1" and cal["kind"] == "ESTIMATED"
    # a mission on the calibrated revision now predicts the measured apogee
    md = m.to_dict()
    md.update(id="calibrated", name="calibrated", revision="REV-B")
    call("POST", "/api/missions", md)
    after = call("POST", "/api/missions/calibrated/simulate")[1]["summary"]["apogee_agl_m"]
    assert abs(after - an["actual"]["apogee_agl_m"]) < abs(before - an["actual"]["apogee_agl_m"]) / 3
    twin = call("GET", f"/api/vehicles/{m.vehicle_id}/twin")[1]
    assert twin["flights"][0]["status"] == "DEVIATION" and twin["calibrations"][0]["revision"] == "REV-B"
    assert call("POST", "/api/flights/F-1/adopt", {"cd_scale": 9})[0] == 400
