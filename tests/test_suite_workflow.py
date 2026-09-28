"""End-to-end workflow through the application service layer / API router:
design -> motor -> mission -> simulate -> Monte Carlo -> SIL -> readiness ->
flight record -> ground-station recording -> log import -> analysis."""

import base64
import socket
import stat
import time
from pathlib import Path

import numpy as np
import pytest

from aerodyne.analysis.flightlog import ColumnMapping, load_flight_log, sniff
from aerodyne.analysis.reconstruction import FlightReconstructionEngine
from aerodyne.app.server import App
from aerodyne.core.provenance import DataQuality
from aerodyne.dynamics.simulator import FlightSimulator
from aerodyne.examples import example_config
from aerodyne.propulsion.formats import write_eng
from aerodyne.propulsion.motor import synthetic_motor
from aerodyne.sil.faults import STANDARD_SCENARIOS
from aerodyne.sil.runner import SILRunner
from aerodyne.sil.virtual_sensors import VirtualSensors
from aerodyne.workspace import Workspace, WorkspaceError


def _call(app, method, path, body=None, query=None, expect=200):
    code, out = app.dispatch(method, path, query or {}, body or {})
    assert code == expect, out
    return out


def _wait(app, job):
    for _ in range(600):
        j = _call(app, "GET", f"/api/jobs/{job}")
        if j["status"] != "running":
            assert j["status"] == "done", j
            return j
        time.sleep(0.2)
    raise AssertionError("job did not finish")


@pytest.fixture()
def app(tmp_path):
    return App(Workspace.init(tmp_path / "ws", "test", author="tester"))


def _log_text(sim, rate=50, ft=True, seed=1):
    vs = VirtualSensors(sim, seed=seed)
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(int((vs.t_end + vs.pad_time) * rate)):
        t = -vs.pad_time + k / rate
        tr = vs.truth(t)
        alt = vs.baro_model.altitude_from_pressure(tr["p"]) - sim.site.altitude_msl + rng.normal(0, 0.4)
        rows.append((int((t + vs.pad_time) * 1000), alt / (0.3048 if ft else 1), (tr["sf"][0] + rng.normal(0, 0.3)) / 9.80665))
    return "; vendor export\nTime(ms),Alt (ft),Accel (G)\n" + "".join(f"{t},{a:.1f},{g:.3f}\n" for t, a, g in rows)


def test_flight_log_sniff_and_units(nominal_sim):
    text = _log_text(nominal_sim)
    sn = sniff(text)
    m = sn["mapping"]
    assert (m["time"], m["time_unit"], m["altitude"], m["altitude_unit"], m["accel"], m["accel_unit"]) == \
        ("Time(ms)", "ms", "Alt (ft)", "ft", "Accel (G)", "g")
    truth = nominal_sim.summary()
    for mapping in (ColumnMapping.from_dict(m), ColumnMapping.from_dict({**m, "accel": None})):
        s = FlightReconstructionEngine(load_flight_log(text, mapping, "F", "t")).run().summary()
        assert s["apogee_agl_m"] == pytest.approx(truth["apogee_agl_m"], rel=0.01)
        assert s["max_vertical_velocity_mps"] == pytest.approx(truth["max_vertical_velocity_mps"], rel=0.05)
        assert s["time_to_apogee_s"] == pytest.approx(truth["time_to_apogee_s"], abs=0.6)


def test_pressure_log_and_bad_mapping():
    t = np.arange(0, 20, 0.05)
    alt = np.where(t < 2, 0, np.minimum(80 * (t - 2), 500))
    from aerodyne.environment.atmosphere import StandardAtmosphere

    atm = StandardAtmosphere()
    text = "t,P (hPa)\n" + "".join(f"{a:.2f},{atm.at(100 + h).pressure / 100:.3f}\n" for a, h in zip(t, alt))
    sn = sniff(text)
    assert sn["mapping"]["pressure"] == "P (hPa)" and sn["mapping"]["pressure_unit"] == "hPa"
    ds = load_flight_log(text, ColumnMapping.from_dict(sn["mapping"]), "F", "t")
    assert ds.v("baro_alt").max() - ds.v("baro_alt").min() == pytest.approx(500, abs=1)
    with pytest.raises(ValueError):
        load_flight_log(text, ColumnMapping(time="nope", altitude="x"), "F", "t")


def test_workspace_rules(tmp_path):
    ws = Workspace.init(tmp_path / "w", "p")
    with pytest.raises(WorkspaceError):
        Workspace.init(tmp_path / "w", "again")
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path / "nothing")
    m = ws.list_missions()[0]
    f = ws.create_flight("F1", m.vehicle_id, "REV-A", m.motor_key, "2026-01-01")
    assert f["configuration"]["revision"] == "REV-A"
    # flown revision is locked; edits need a new revision
    with pytest.raises(WorkspaceError):
        ws.save_design(m.vehicle_id, "REV-A", ws.design(m.vehicle_id, "REV-A"))
    e = ws.add_raw_file("F1", "log.csv", b"t,alt\n0,0\n")
    p = ws.raw_path("F1", "log.csv")
    assert not (p.stat().st_mode & stat.S_IWUSR)                       # read-only on disk
    with pytest.raises(WorkspaceError):
        ws.add_raw_file("F1", "log.csv", b"different")                 # never overwritten
    p.chmod(0o644)
    p.write_bytes(b"tampered")
    with pytest.raises(WorkspaceError, match="SHA-256"):
        ws.raw_path("F1", "log.csv")
    assert e["kind"] == "MEASURED"
    with pytest.raises(WorkspaceError):
        ws.create_flight("F1", m.vehicle_id, "REV-A", m.motor_key, "x")


def test_full_workflow(app, tmp_path):
    ws = app.ws
    # design: new vehicle from template, analysis, revision workflow
    v = _call(app, "POST", "/api/vehicles", {"name": "Test rocket", "template": "example"})
    vid = v["vehicle_id"]
    det = _call(app, "GET", f"/api/vehicles/{vid}")
    design = det["design"]
    motors = _call(app, "GET", "/api/motors")
    an = _call(app, "POST", "/api/analyze", {"design": design, "motor_key": motors[0]["key"]})
    assert an["margins_cal"]["FULL"] > 1.5 and an["profile"] and an["mass"]["FULL"]["kind"] == "ESTIMATED"
    for c in design["vehicle"]["components"]:
        c["mass_override"] = round(c.get("mass_estimate", 0.05) or 0.05, 3) if c["type"] == "PointMass" else None
    _call(app, "PUT", f"/api/vehicles/{vid}/REV-A", {"design": design})
    _call(app, "POST", "/api/analyze", {"design": {"vehicle": {"name": "x", "components": [{"type": "Nope"}]}}}, expect=400)

    # a certified motor (the synthetic curve re-labelled for the test only)
    p = tmp_path / "m.eng"
    write_eng(synthetic_motor("TEST-H300"), p)
    keys = _call(app, "POST", "/api/motors/import", {"text": p.read_text(), "quality": "CERTIFIED",
                                                    "source": "unit test", "source_date": "2026-01-01"})["keys"]
    curve = _call(app, "GET", f"/api/motors/{keys[0]}")
    assert curve["data_quality"] == DataQuality.CERTIFIED.value and len(curve["time"]) > 10

    # mission with limits
    mission = {"id": "field-day", "name": "Field day", "vehicle_id": vid, "revision": "REV-A", "motor_key": keys[0],
               "site": {"altitude_msl": 100, "latitude": 35, "longitude": -117, "rail_length": 1.8,
                        "elevation_deg": 87, "azimuth_deg": 270},
               "wind": {"model": "power_law", "speed": 3, "from_deg": 270},
               "atmosphere": {"temperature_offset": 0, "sea_level_pressure": 101325, "relative_humidity": 0},
               "limits": {"altitude_ceiling_agl_m": 1500, "field_radius_m": 1500},
               "uncertainty": _call(app, "GET", "/api/missions/example")["uncertainty"]}
    _call(app, "POST", "/api/missions", mission)
    sim = _call(app, "POST", "/api/missions/field-day/simulate")
    assert sim["kind"] == "simulation" and sim["data_kind"] == "SIMULATED"
    assert 500 < sim["summary"]["apogee_agl_m"] < 1200 and len(sim["series"]["t"]) <= 900

    # readiness blocks until the SIL suite has run for exactly this configuration
    r0 = _call(app, "POST", "/api/missions/field-day/readiness")
    assert r0["status"] == "NO-GO" and "Flight software fault suite (SIL)" in r0["blocking"]
    _wait(app, _call(app, "POST", "/api/missions/field-day/montecarlo", {"n": 6})["job"])
    _wait(app, _call(app, "POST", "/api/missions/field-day/sil", {"backend": "python"})["job"])
    r1 = _call(app, "POST", "/api/missions/field-day/readiness")
    assert r1["blocking"] == [] and r1["status"] in ("GO", "GO WITH WARNINGS")
    assert r1["evidence_runs"]["montecarlo"] and r1["evidence_runs"]["sil"]
    checks = {c["id"]: c for c in r1["checks"]}
    assert checks["motor_data"]["status"] == "PASS" and checks["dispersion"]["status"] == "PASS"
    # changing the mission makes the evidence stale
    mission["wind"]["speed"] = 6
    _call(app, "POST", "/api/missions", mission)
    assert _call(app, "POST", "/api/missions/field-day/readiness")["evidence_runs"] == {"montecarlo": None, "sil": None}

    # flight record: revision becomes FLOWN and locked
    _call(app, "POST", "/api/flights", {"flight_id": "F-001", "vehicle_id": vid, "revision": "REV-A",
                                        "motor_key": keys[0], "mission_id": "field-day", "date": "2026-10-01"})
    assert ws.vehicle_summary(vid)["revisions"][0]["status"] == "FLOWN"
    _call(app, "PUT", f"/api/vehicles/{vid}/REV-A", {"design": design}, expect=400)
    rev = _call(app, "POST", f"/api/vehicles/{vid}/revise", {"design": design | {"notes": "post-flight"},
                                                             "note": "after F-001"})
    assert rev["revision"] == "REV-B"

    # ground station: record real (UDP) telemetry into the flight
    port = _free_udp_port()
    _call(app, "POST", "/api/ground/start", {"source": {"type": "udp", "port": port}, "flight_id": "F-001"})
    cfg_sim = FlightSimulator(example_config()).run()
    frames = SILRunner(cfg_sim, []).run().radio_frames
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    time.sleep(0.3)
    for i, (_, data) in enumerate(frames):
        sock.sendto(data, ("127.0.0.1", port))
        if i % 20 == 0:
            time.sleep(0.002)      # pace like a radio link; a burst overflows the socket buffer
    time.sleep(1.0)
    st = _call(app, "GET", "/api/ground/status")
    assert st["active"] and st["bytes_received"] >= 0.95 * sum(len(d) for _, d in frames)
    stop = _call(app, "POST", "/api/ground/stop")
    cap = stop["flight_file"]
    assert cap["name"].endswith(".cap") and cap["kind"] == "MEASURED"

    # analyse the telemetry capture and an uploaded altimeter log
    a1 = _call(app, "POST", "/api/flights/F-001/analyze", {"file": cap["name"]})
    assert a1["actual"]["apogee_agl_m"] == pytest.approx(cfg_sim.summary()["apogee_agl_m"], rel=0.03)
    up = _call(app, "POST", "/api/flights/F-001/files", {"name": "alt.csv", "b64": base64.b64encode(
        _log_text(cfg_sim).encode()).decode()})
    a2 = _call(app, "POST", "/api/flights/F-001/analyze", {"file": "alt.csv", "mapping": up["sniff"]["mapping"]})
    assert a2["actual"]["apogee_agl_m"] == pytest.approx(cfg_sim.summary()["apogee_agl_m"], rel=0.02)
    assert a2["comparison"] and a2["status"] in ("VALIDATED", "DEVIATION")
    cal = _call(app, "POST", "/api/flights/F-001/calibrate")
    assert cal["proposal"] is None or cal["proposal"]["kind"] == "ESTIMATED"
    assert ws.flight_analysis("F-001")["file"] == "alt.csv"


def test_rehearsal_never_filed_as_flight_data(app):
    m = app.ws.list_missions()[0]
    app.ws.create_flight("F-R", m.vehicle_id, "REV-A", m.motor_key, "2026-01-01")
    _call(app, "POST", "/api/ground/start", {"source": {"type": "sil", "mission_id": m.id, "speed": 200},
                                             "flight_id": "F-R"})
    _call(app, "POST", "/api/ground/start", {"source": {"type": "udp", "port": 1}}, expect=400)  # one session
    time.sleep(1.5)
    out = _call(app, "POST", "/api/ground/stop")
    assert "flight_file" not in out and "simulated" in out["note"]
    assert app.ws.flight("F-R")["raw_files"] == []
    assert Path(out["capture"]).exists()              # but the capture itself is kept


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_landing_detected_with_failed_baro_marginal_design(seed):
    """Regression: GNSS landing fallback used an end-point velocity that noise
    could keep above threshold; now a least-squares slope."""
    from aerodyne.aero.analytical import AnalyticalAeroModel

    cfg = example_config()
    next(c for c in cfg.vehicle.components if c.name == "fins").span = 0.05
    cfg.aero = AnalyticalAeroModel(cfg.vehicle)
    sim = FlightSimulator(cfg).run()
    r = SILRunner(sim, STANDARD_SCENARIOS["baro_failure_boost"], seed=seed).run()
    assert r.final_state == "LANDED" and r.evaluate() == []


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
