"""Service layer: every operation the UI (or a script) can perform on a
workspace, returning JSON-ready dicts. The HTTP server is a thin router on top.

Stale-result rule: every saved result records the hashes of the design,
motor dataset and mission it was computed from. Consumers (readiness, flight
comparison) only use results whose hashes match the current configuration.
"""

from __future__ import annotations

import base64
import threading
import traceback
import uuid
from typing import Any, Callable

import numpy as np

from aerodyne.aero.analytical import AnalyticalAeroModel, barrowman_cp
from aerodyne.analysis import comparison, model_error
from aerodyne.analysis.flightlog import ColumnMapping, load_flight_log, sniff
from aerodyne.analysis.reconstruction import FlightReconstructionEngine
from aerodyne.app.schema import profile, schema
from aerodyne.components_util import decimate_indices
from aerodyne.core.provenance import DataKind, DataQuality, stable_hash
from aerodyne.dynamics.simulator import FlightSimulator, SimulationConfig, SimulationResult
from aerodyne.montecarlo.engine import MonteCarloEngine
from aerodyne.readiness import review
from aerodyne.twin.digital_twin import DigitalTwin
from aerodyne.vehicle.mass import MassPropertiesEngine
from aerodyne.vehicle.vehicle import Vehicle
from aerodyne.workspace import Design, Mission, Workspace, WorkspaceError, design_from_payload


class BadRequest(ValueError):
    pass


def _clean(obj: Any) -> Any:
    """JSON-safe: numpy -> python, NaN/inf -> None."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return _clean(obj.tolist())
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return f if np.isfinite(f) else None
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


# ---------------------------------------------------------------------------- design
def get_schema() -> dict:
    return schema()


def parse_design(payload: dict) -> Design:
    try:
        return design_from_payload(payload)
    except (TypeError, KeyError, ValueError, AttributeError) as exc:
        raise BadRequest(f"invalid design: {exc}") from exc


def analyze_design(ws: Workspace, payload: dict, motor_key: str | None = None) -> dict:
    """Live designer analysis: mass properties, CP, margins, profile, warnings."""
    d = parse_design(payload)
    v = d.vehicle
    eng = MassPropertiesEngine(v)
    motor = ws.motor(motor_key) if motor_key else None
    out: dict[str, Any] = {"warnings": v.validate(), "profile": profile(v)}
    try:
        out["length_m"] = v.length
        out["reference_diameter_m"] = v.reference_diameter
    except ValueError as exc:
        out["warnings"].append(str(exc))
        return _clean(out)
    configs = {}
    for name in ("EMPTY", "FULL", "MOTOR_SPENT", "PAYLOAD_REMOVED"):
        try:
            mp = eng.configuration(name, motor)
            configs[name] = {"mass_kg": mp.mass, "cg_m": mp.cg, "ixx": mp.ixx, "iyy": mp.iyy,
                             "kind": mp.kind.value}
        except ValueError:
            pass
    out["mass"] = configs
    out["components"] = [{"name": c.name, "mass_kg": c.mass, "cg_m": c.cg, "kind": c.mass_kind.value}
                         for c in v.components if c is not v.motor_slot]
    try:
        cna, xcp, parts = barrowman_cp(v, 0.3)
        out["cp_m"], out["cn_alpha"] = xcp, cna
        out["cp_parts"] = [{"name": n, "cn_alpha": a, "x": x} for n, a, x in parts]
        d_ref = v.reference_diameter
        out["margins_cal"] = {k: (xcp - c["cg_m"]) / d_ref for k, c in configs.items()}
        if motor is None:
            out["warnings"].append("select a motor to see loaded-vehicle stability")
        elif out["margins_cal"].get("FULL", 0) < 1.0:
            out["warnings"].append("static margin with motor loaded is below 1 caliber")
        aero = AnalyticalAeroModel(v)
        out["calibration"] = d.calibration or None
        out["cd0_curve"] = [{"mach": m, "cd": d.cd_scale * aero.zero_lift_cd(m, 6e6)} for m in
                            np.round(np.linspace(0.1, 2.0, 20), 2)]
    except ValueError as exc:
        out["warnings"].append(str(exc))
    if motor is not None:
        out["motor"] = motor.summary()
    return _clean(out)


def template_design(kind: str) -> Design:
    from aerodyne.examples import example_recovery, example_vehicle

    if kind == "example":
        return Design(example_vehicle(), example_recovery())
    from aerodyne.app.schema import DEFAULTS
    from aerodyne.vehicle import components as comp

    v = Vehicle("new vehicle")
    v.add(comp.NoseCone(x=0.0, **DEFAULTS["NoseCone"]))
    v.add(comp.BodyTube(x=0.3, **{**DEFAULTS["BodyTube"], "length_": 0.9}))
    v.add(comp.PointMass(x=0.6, **{**DEFAULTS["PointMass"], "name": "avionics", "mass_estimate": 0.2,
                                   "tags": {"avionics"}}))
    v.add(comp.FinSet(x=1.06, **DEFAULTS["FinSet"]))
    v.add(comp.MotorSlot(x=0.95, **DEFAULTS["MotorSlot"]))
    from aerodyne.recovery.recovery import RecoveryConfig, RecoveryDevice

    return Design(v, RecoveryConfig(devices=(RecoveryDevice("parachute", 1.5, 0.9),)))


def create_vehicle(ws: Workspace, name: str, template: str = "blank", ork_b64: str | None = None,
                   author: str = "unknown") -> dict:
    warnings: list[str] = []
    if ork_b64:
        import tempfile
        from pathlib import Path

        from aerodyne.interop.openrocket import read_ork

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "design.ork"
            p.write_bytes(base64.b64decode(ork_b64))
            try:
                imp = read_ork(p)
            except Exception as exc:
                raise BadRequest(f"could not read OpenRocket file: {exc}") from exc
        design = Design(imp.vehicle, imp.recovery, notes=f"imported from OpenRocket ({imp.creator})")
        warnings = imp.warnings + [f"motor in .ork: {m.manufacturer} {m.designation} - import its "
                                   "certified curve in Motors" for m in imp.motors]
    else:
        design = template_design(template)
    design.vehicle.name = name
    out = ws.create_vehicle(name, design, author=author)
    return {**out, "warnings": warnings}


def vehicle_detail(ws: Workspace, vehicle_id: str, revision: str | None = None) -> dict:
    summ = ws.vehicle_summary(vehicle_id)
    label = revision or summ["revisions"][-1]["label"]
    return {**summ, "revision": label, "design": ws.revision_payload(vehicle_id, label)}


# ------------------------------------------------------------------------------ motors
def list_motors(ws: Workspace) -> list[dict]:
    return [{"key": k, **ws.motor(k).summary()} for k in ws.motor_keys()]


def motor_curve(ws: Workspace, key: str) -> dict:
    m = ws.motor(key)
    return _clean({"key": key, **m.summary(), "time": m.time, "thrust": m.thrust,
                   "notes": m.metadata.notes})


def import_motor(ws: Workspace, text: str, quality: str, source: str, source_date: str) -> dict:
    try:
        q = DataQuality(quality)
    except ValueError as exc:
        raise BadRequest(f"unknown data quality {quality}") from exc
    try:
        keys = ws.import_eng(text, q, source or "user import", source_date or "unknown")
    except (ValueError, IndexError) as exc:
        raise BadRequest(f"could not parse .eng data: {exc}") from exc
    return {"keys": keys}


# ------------------------------------------------------------------------------- missions
def aero_for(design: Design):
    """Aero model of a design, including an adopted flight calibration."""
    from aerodyne.aero.model import ScaledAeroModel

    base = AnalyticalAeroModel(design.vehicle)
    return ScaledAeroModel(base, cd_scale=design.cd_scale) if design.cd_scale != 1.0 else base


def mission_hashes(ws: Workspace, m: Mission) -> dict[str, str]:
    return {"design": ws.registry.get(m.vehicle_id).revision(m.revision).config_hash,
            "motor": ws.motor(m.motor_key).data_hash,
            "mission": stable_hash(m.to_dict())}


def build_config(ws: Workspace, m: Mission, **kw: Any) -> tuple[SimulationConfig, Design]:
    d = ws.design(m.vehicle_id, m.revision)
    cfg = SimulationConfig(vehicle=d.vehicle, motor=ws.motor(m.motor_key),
                           aero=aero_for(d), atmosphere=m.atmosphere_model(),
                           wind=m.wind_model(), site=m.launch_site(), recovery=d.recovery, **kw)
    return cfg, d


def save_mission(ws: Workspace, data: dict) -> dict:
    try:
        m = Mission.from_dict(data)
    except TypeError as exc:
        raise BadRequest(f"invalid mission: {exc}") from exc
    try:
        return ws.save_mission(m).to_dict()
    except (WorkspaceError, KeyError) as exc:
        raise BadRequest(str(exc)) from exc


# ---------------------------------------------------------------------------- simulation
def series(res: SimulationResult, max_points: int = 900) -> dict:
    idx = decimate_indices(len(res.t), max_points)
    speed = np.linalg.norm(res.velocity, axis=1)
    return _clean({
        "t": res.t[idx], "altitude": res.position[idx, 2], "east": res.position[idx, 0],
        "north": res.position[idx, 1], "speed": speed[idx], "vz": res.velocity[idx, 2],
        "axial_accel": res.specific_force_body[idx, 0], "mach": res.mach[idx],
        "margin_cal": res.stability_margin[idx], "thrust": res.thrust[idx],
        "alpha_deg": np.degrees(res.alpha[idx]), "phase": [res.phase[i] for i in idx],
    })


def simulate(ws: Workspace, mission_id: str) -> dict:
    m = ws.mission(mission_id)
    cfg, _ = build_config(ws, m)
    res = FlightSimulator(cfg).run()
    data = {"summary": res.summary(), "events": res.events, "notes": res.notes,
            "series": series(res), "hashes": mission_hashes(ws, m),
            "motor_quality": cfg.motor.metadata.data_quality.value, "data_kind": DataKind.SIMULATED.value}
    run_id = ws.save_run("simulation", m.id, _clean(data))
    return ws.run(run_id)


def monte_carlo(ws: Workspace, mission_id: str, n: int, progress: Callable[[int, int], None] | None = None,
                workers: int = 1) -> dict:
    if not 2 <= n <= 5000:
        raise BadRequest("Monte Carlo runs must be between 2 and 5000")
    m = ws.mission(mission_id)
    cfg, _ = build_config(ws, m)
    mc = MonteCarloEngine(cfg, m.uncertainty_model(), dt=0.01).run(n, seed=1, workers=workers,
                                                                    progress=progress)
    rep = mc.report()
    data = {"summary": {"runs": rep["runs"], "failures": rep["failures"],
                        "apogee_p50": rep["statistics"]["apogee_agl_m"].get("p50"),
                        "apogee_p95": rep["statistics"]["apogee_agl_m"].get("p95")},
            "report": rep, "samples": mc.samples,
            "landing": [[o.get("landing_east_m"), o.get("landing_north_m")] for o in mc.outputs],
            "apogees": [o.get("apogee_agl_m") for o in mc.outputs],
            "failures": mc.failures, "hashes": mission_hashes(ws, m), "data_kind": DataKind.SIMULATED.value,
            "note": "Monte Carlo wind uses a power-law profile around the mission's surface wind."}
    run_id = ws.save_run("montecarlo", m.id, _clean(data))
    return ws.run(run_id)


def sil_suite(ws: Workspace, mission_id: str, backend: str = "python",
              progress: Callable[[int, int], None] | None = None) -> dict:
    from aerodyne.sil.faults import STANDARD_SCENARIOS
    from aerodyne.sil.runner import SILRunner

    if backend not in ("python", "c", "c-app"):
        raise BadRequest("backend must be python, c or c-app")
    m = ws.mission(mission_id)
    cfg, _ = build_config(ws, m)
    sim = FlightSimulator(cfg).run()
    results = []
    names = sorted(STANDARD_SCENARIOS)
    for i, name in enumerate(names):
        try:
            r = SILRunner(sim, STANDARD_SCENARIOS[name], backend=backend).run()
            results.append({"scenario": name, "issues": r.evaluate(), "final_state": r.final_state,
                            "transitions": [[t, s, reason] for t, _, s, reason in r.transitions if t >= 0],
                            "faults_detected": r.faults_detected, "est_apogee": r.est_apogee,
                            "true_apogee": r.true_apogee, "link": r.link})
        except Exception as exc:          # a crash is a failed scenario, never hidden
            results.append({"scenario": name, "issues": [f"crashed: {exc!r}"], "final_state": None,
                            "transitions": [], "faults_detected": [], "link": {}})
        if progress:
            progress(i + 1, len(names))
    failed = sum(1 for r in results if r["issues"])
    data = {"summary": {"passed": len(results) - failed, "total": len(results), "backend": backend},
            "results": results, "backend": backend, "hashes": mission_hashes(ws, m),
            "data_kind": DataKind.SIMULATED.value}
    run_id = ws.save_run("sil", m.id, _clean(data))
    return ws.run(run_id)


def _latest_matching(ws: Workspace, mission: Mission, kind: str) -> dict | None:
    want = mission_hashes(ws, mission)
    for r in ws.list_runs(mission.id, kind):
        full = ws.run(r["id"])
        if full.get("hashes") == want:
            return full
    return None


def readiness(ws: Workspace, mission_id: str) -> dict:
    m = ws.mission(mission_id)
    cfg, design = build_config(ws, m)
    nominal = FlightSimulator(cfg).run()
    mc = _latest_matching(ws, m, "montecarlo")
    sil = _latest_matching(ws, m, "sil")
    rep = review(design, cfg.motor, m.limits, nominal, mc["report"] if mc else None, sil)
    rep["evidence_runs"] = {"montecarlo": mc["id"] if mc else None, "sil": sil["id"] if sil else None}
    rep["hashes"] = mission_hashes(ws, m)
    run_id = ws.save_run("readiness", m.id, _clean({**rep, "summary": {"status": rep["status"]}}))
    return ws.run(run_id)


# ------------------------------------------------------------------------------ flights
def flight_config(ws: Workspace, rec: dict) -> tuple[SimulationConfig, Design, str]:
    """Prediction for exactly what flew: the flown revision + motor, in the mission's
    environment when the flight is linked to a mission."""
    d = ws.design(rec["vehicle_id"], rec["revision"])
    motor = ws.motor(rec["motor_key"])
    kw: dict[str, Any] = {}
    basis = "standard atmosphere, no wind"
    if rec.get("mission_id"):
        m = ws.mission(rec["mission_id"])
        kw = {"atmosphere": m.atmosphere_model(), "wind": m.wind_model(), "site": m.launch_site()}
        basis = f"mission '{m.name}' environment"
    cfg = SimulationConfig(vehicle=d.vehicle, motor=motor, aero=aero_for(d),
                           recovery=d.recovery, **kw)
    if d.cd_scale != 1.0:
        basis += f"; drag calibrated x{d.cd_scale:.3f} from {d.calibration.get('source_flight', '?')}"
    return cfg, d, basis


def create_flight(ws: Workspace, data: dict) -> dict:
    try:
        return ws.create_flight(data["flight_id"], data["vehicle_id"], data["revision"],
                                data["motor_key"], data.get("date", ""), data.get("mission_id"),
                                data.get("notes", ""), data.get("hardware"))
    except KeyError as exc:
        raise BadRequest(f"missing field {exc}") from exc
    except Exception as exc:
        raise BadRequest(str(exc)) from exc


def upload_flight_file(ws: Workspace, flight_id: str, name: str, b64: str, source: str = "") -> dict:
    raw = base64.b64decode(b64)
    entry = ws.add_raw_file(flight_id, name, raw, source)
    if entry["name"].endswith(".cap"):
        return {"file": entry, "sniff": {"format": "telemetry_capture", "columns": [], "rows": None,
                                         "mapping": {"format": "telemetry_capture"}, "warnings": []}}
    try:
        info = sniff(raw.decode("utf-8", errors="replace"))
    except ValueError as exc:
        info = {"error": str(exc)}
    return {"file": entry, "sniff": info}


def sniff_flight_file(ws: Workspace, flight_id: str, name: str) -> dict:
    text = ws.raw_path(flight_id, name).read_text(errors="replace")
    return sniff(text)


def analyze_flight(ws: Workspace, flight_id: str, file_name: str, mapping: dict) -> dict:
    rec = ws.flight(flight_id)
    path = ws.raw_path(flight_id, file_name)                           # verifies SHA-256
    src = f"{flight_id}/raw/{file_name}"
    try:
        if file_name.endswith(".cap") or mapping.get("format") == "telemetry_capture":
            from aerodyne.analysis.telemetry_log import dataset_from_frames
            from aerodyne.ground.server import read_capture

            cm = ColumnMapping(time="timestamp_ms")
            mapping_out = {"format": "telemetry_capture"}
            ds = dataset_from_frames(read_capture(path), flight_id, src)
        else:
            cm = ColumnMapping.from_dict(mapping)
            mapping_out = cm.to_dict()
            ds = load_flight_log(path.read_text(errors="replace"), cm, flight_id, source=src)
        rec_flight = FlightReconstructionEngine(ds).run()
    except (ValueError, KeyError) as exc:
        raise BadRequest(f"could not analyse log: {exc}") from exc
    actual = rec_flight.summary()
    cfg, d, basis = flight_config(ws, rec)
    motor = cfg.motor
    predicted_res = FlightSimulator(cfg).run()
    predicted = predicted_res.summary()
    rows = comparison.compare(predicted, actual)
    contributors = model_error.diagnose(predicted, actual)
    twin = DigitalTwin(rec["vehicle_id"], rec["revision"], d.vehicle, motor, d.recovery)
    validation = twin.validate_against_flight(flight_id, predicted, actual)
    idx = decimate_indices(len(rec_flight.t), 900)
    analysis = {
        "file": file_name, "mapping": mapping_out, "actual": actual, "predicted": predicted,
        "actual_kind": rec_flight.kind.value, "notes": rec_flight.notes, "phases": rec_flight.phases,
        "status": validation.status, "validation": validation.metrics,
        "comparison": [{"key": r.key, "label": r.label, "units": r.units, "simulated": r.simulated,
                        "actual": r.actual, "abs_error": r.abs_error, "pct_error": r.pct_error}
                       for r in rows],
        "contributors": [c.__dict__ for c in contributors],
        "actual_series": {"t": rec_flight.t[idx], "altitude": rec_flight.altitude[idx],
                          "velocity": rec_flight.velocity[idx], "accel": rec_flight.acceleration[idx],
                          "altitude_sigma": rec_flight.altitude_sigma[idx]},
        "predicted_series": series(predicted_res),
        "motor_quality": motor.metadata.data_quality.value,
        "prediction_basis": basis,
    }
    ws.save_flight_analysis(flight_id, _clean(analysis))
    return ws.flight_analysis(flight_id)


def propose_calibration(ws: Workspace, flight_id: str) -> dict:
    rec = ws.flight(flight_id)
    an = ws.flight_analysis(flight_id)
    if not an:
        raise BadRequest("analyse the flight first")
    base, d, _ = flight_config(ws, rec)
    motor = base.motor
    twin = DigitalTwin(rec["vehicle_id"], rec["revision"], d.vehicle, motor, d.recovery)
    try:
        prop = twin.propose_drag_calibration(an["actual"]["apogee_agl_m"], base)
    except ValueError as exc:
        return {"proposal": None, "reason": str(exc)}
    return {"proposal": {"parameter": "drag coefficient scale", "value": prop.value,
                         "kind": prop.kind.value, "note": prop.note},
            "contributors": an.get("contributors", []),
            "reason": "matches the measured apogee by scaling drag alone; other contributors listed "
                      "may explain the difference instead - review before adopting"}


def adopt_calibration(ws: Workspace, flight_id: str, cd_scale: float, note: str = "",
                      author: str = "unknown") -> dict:
    """Close the loop (spec 49): the flown revision stays locked; the calibrated twin
    becomes a NEW revision of the vehicle, with provenance, used by later simulations."""
    from datetime import datetime, timezone

    if not 0.3 <= cd_scale <= 3.0:
        raise BadRequest("drag scale must be between 0.3 and 3.0")
    rec = ws.flight(flight_id)
    an = ws.flight_analysis(flight_id)
    if not an:
        raise BadRequest("analyse the flight before adopting a calibration")
    latest = ws.vehicle_summary(rec["vehicle_id"])["revisions"][-1]["label"]
    d = ws.design(rec["vehicle_id"], latest)
    prev = d.cd_scale
    d.calibration = {"cd_scale": round(cd_scale, 4), "source_flight": flight_id,
                     "source_revision": rec["revision"], "method": "apogee match (drag scale only)",
                     "measured_apogee_agl_m": (an.get("actual") or {}).get("apogee_agl_m"),
                     "kind": "ESTIMATED", "adopted": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "previous_cd_scale": prev, "note": note}
    out = ws.revise(rec["vehicle_id"], d, f"drag calibration x{cd_scale:.3f} from flight {flight_id}"
                    + (f": {note}" if note else ""), author=author)
    return {"vehicle_id": rec["vehicle_id"], "revision": out["revision"], "calibration": d.calibration}


def twin_history(ws: Workspace, vehicle_id: str) -> dict:
    """Every flight of a vehicle with its validation status and any calibration adopted."""
    flights = [f for f in ws.list_flights() if f["vehicle_id"] == vehicle_id]
    rows = []
    for f in flights:
        an = ws.flight_analysis(f["flight_id"]) or {}
        apo = next((r for r in an.get("comparison", []) if r["key"] == "apogee_agl_m"), None)
        rows.append({"flight_id": f["flight_id"], "date": f.get("date"), "revision": f["revision"],
                     "status": an.get("status"), "apogee_error_pct": apo and apo["pct_error"],
                     "prediction_basis": an.get("prediction_basis")})
    cals = []
    for r in ws.vehicle_summary(vehicle_id)["revisions"]:
        c = ws.design(vehicle_id, r["label"]).calibration
        if c:
            cals.append({"revision": r["label"], **c})
    return {"flights": rows, "calibrations": cals}


def cad_part(data: dict) -> dict:
    """Mass properties of an uploaded STEP or STL file -> CadPart component dicts."""
    import tempfile
    from pathlib import Path

    from aerodyne.cad.stl import stl_part
    from aerodyne.cad.step import step_parts

    name = data.get("name") or "part"
    suffix = Path(data.get("filename", name)).suffix.lower()
    density = float(data["density"])
    axis = data.get("axis", "+z")
    station = float(data.get("nose_station", 0.0))
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f"upload{suffix}"
        p.write_bytes(base64.b64decode(data["b64"]))
        try:
            if suffix in (".step", ".stp"):
                parts = step_parts(p, density, axis, station, name, merge=bool(data.get("merge", True)))
            elif suffix == ".stl":
                parts = [stl_part(p, density, name, data.get("units", "mm"), axis, station)]
            else:
                raise BadRequest("CAD file must be .step, .stp or .stl")
        except (RuntimeError, ValueError) as exc:
            raise BadRequest(str(exc)) from exc
    comps = []
    for cp in parts:
        c = cp.to_component(length=float(data.get("length", 0.0)))
        c.source = data.get("filename", c.source)
        d = {f: getattr(c, f) for f in ("name", "x", "cad_mass", "cad_cg", "cad_ixx", "cad_iyy",
                                        "length_", "source", "source_sha256", "mass_override", "cg_override")}
        comps.append({"type": "CadPart", **d, "tags": [], "warnings": list(cp.warnings), "kind": cp.kind.value})
    return {"components": _clean(comps)}


# ----------------------------------------------------------------------------------- jobs
class JobManager:
    """Runs long operations (Monte Carlo, SIL suite) in background threads."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def start(self, kind: str, fn: Callable[[Callable[[int, int], None]], dict]) -> str:
        jid = uuid.uuid4().hex[:10]
        with self.lock:
            self.jobs[jid] = {"id": jid, "kind": kind, "status": "running", "done": 0, "total": 0,
                              "result": None, "error": None}

        def progress(i: int, n: int) -> None:
            with self.lock:
                self.jobs[jid].update(done=i, total=n)

        def run() -> None:
            try:
                res = fn(progress)
                with self.lock:
                    self.jobs[jid].update(status="done", result_id=res.get("id"))
            except Exception as exc:
                with self.lock:
                    self.jobs[jid].update(status="error", error=f"{exc}",
                                          trace=traceback.format_exc(limit=3))

        threading.Thread(target=run, daemon=True).start()
        return jid

    def get(self, jid: str) -> dict[str, Any]:
        with self.lock:
            if jid not in self.jobs:
                raise BadRequest(f"unknown job {jid}")
            return {k: v for k, v in self.jobs[jid].items() if k != "result"}
