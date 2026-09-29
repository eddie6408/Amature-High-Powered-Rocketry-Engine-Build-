"""Cross-validate an imported OpenRocket design against OpenRocket's own numbers.

When an ``.ork`` file was saved with simulation data, it contains OpenRocket's
computed mass, motor mass, CG, CP and drag coefficient over time. This module
compares AERODYNE's independent calculation (import + mass-properties engine +
Barrowman + drag build-up) with those values. Neither tool is assumed correct:
the result is a table of differences to investigate.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from aerodyne.aero.analytical import AnalyticalAeroModel, barrowman_cp
from aerodyne.interop.openrocket import _load_xml, read_ork
from aerodyne.vehicle.mass import MassPropertiesEngine


@dataclass
class OrSimulation:
    name: str
    columns: dict[str, np.ndarray]


def stored_simulations(root: ET.Element) -> list[OrSimulation]:
    out = []
    for sim in root.findall("./simulations/simulation"):
        br = sim.find("./flightdata/databranch")
        if br is None or not br.get("types"):
            continue
        types = br.get("types").split(",")
        rows = []
        for dp in br.findall("datapoint"):
            vals = [float(v) if v not in ("NaN", "") else math.nan for v in (dp.text or "").split(",")]
            if len(vals) == len(types):
                rows.append(vals)
        if rows:
            arr = np.array(rows)
            out.append(OrSimulation(sim.findtext("name") or "simulation",
                                    {t: arr[:, i] for i, t in enumerate(types)}))
    return out


def _at_mach(sim: OrSimulation, key: str, mach: float) -> float | None:
    c = sim.columns
    if key not in c or "Mach number" not in c:
        return None
    m, v = c["Mach number"], c[key]
    i_peak = int(np.nanargmax(m))
    sel = np.arange(i_peak + 1)                       # ascent / boost only
    ok = sel[np.isfinite(v[sel]) & np.isfinite(m[sel]) & (m[sel] > 0.02)]
    if len(ok) == 0:
        return None
    return float(v[ok[np.argmin(np.abs(m[ok] - mach))]])


def validate(path: str | Path, mach: float = 0.3) -> dict:
    imp = read_ork(path)
    sims = stored_simulations(_load_xml(path))
    rows: list[dict] = []
    v = imp.vehicle
    eng = MassPropertiesEngine(v)
    result = {"file": str(path), "warnings": imp.warnings, "rows": rows, "simulation": None}
    if not sims:
        result["note"] = "file has no stored OpenRocket simulation data - re-save it in OpenRocket " \
                         "after running a simulation to enable cross-validation"
        return result
    sim = sims[0]
    result["simulation"] = sim.name
    c = sim.columns

    def add(name: str, ours: float | None, theirs: float | None, unit: str, note: str = "") -> None:
        diff = None if ours is None or theirs is None else ours - theirs
        rel = None if diff is None or not theirs else diff / abs(theirs)
        rows.append({"quantity": name, "aerodyne": ours, "openrocket": theirs, "difference": diff,
                     "relative": rel, "unit": unit, "note": note})

    mass0 = c.get("Mass", [math.nan])[0]
    motor0 = c.get("Motor mass", [0.0])[0]
    dry = eng.dry()
    add("Dry mass (without motor)", dry.mass, float(mass0 - motor0) if np.isfinite(mass0) else None, "kg",
        "OpenRocket: Mass - Motor mass at t=0")
    if v.motor_slot is not None and np.isfinite(motor0) and motor0 > 0:
        full = eng.at_motor_mass(float(motor0))
        add("CG with motor, t=0", full.cg, float(c.get("CG location", [math.nan])[0]), "m",
            "motor CG assumed at the middle of its slot")
    try:
        _, xcp, _ = barrowman_cp(v, mach)
        add(f"CP at Mach {mach}", xcp, _at_mach(sim, "CP location", mach), "m")
    except ValueError:
        pass
    try:
        aero = AnalyticalAeroModel(v)
        rey = _at_mach(sim, "Reynolds number", mach)
        cd_or = _at_mach(sim, "Axial drag coefficient", mach) or _at_mach(sim, "Drag coefficient", mach)
        add(f"Zero-lift Cd at Mach {mach}", aero.zero_lift_cd(mach, (rey or 5e6 * v.length) / v.length, False),
            cd_or, "-", "OpenRocket value is during boost (base drag reduced by thrust)")
    except ValueError:
        pass
    return result


def render(res: dict) -> str:
    lines = [f"OpenRocket cross-validation: {res['file']}"]
    if res.get("note"):
        return "\n".join(lines + [res["note"]])
    lines.append(f"stored simulation: {res['simulation']}")
    lines.append(f"{'quantity':30s}{'AERODYNE':>12s}{'OpenRocket':>12s}{'diff':>10s}{'rel':>8s}")
    for r in res["rows"]:
        f = lambda x: "—" if x is None else f"{x:.4g}"
        rel = "—" if r["relative"] is None else f"{100 * r['relative']:+.1f}%"
        lines.append(f"{r['quantity']:30s}{f(r['aerodyne']):>12s}{f(r['openrocket']):>12s}"
                     f"{f(r['difference']):>10s}{rel:>8s}  {r['unit']}")
    unsupported = [w for w in res["warnings"] if "unsupported" in w or "approximated" in w]
    if unsupported:
        lines += ["", "import notes:"] + [f"  - {w}" for w in unsupported]
    return "\n".join(lines)
