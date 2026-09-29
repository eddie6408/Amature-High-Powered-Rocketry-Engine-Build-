"""Design readiness review: "is this design ready to build / fly?"

Collects the evidence the platform can produce *before* anything is built -
geometry checks, stability, rail exit, thrust-to-weight, model validity,
altitude ceiling, landing dispersion, recovery rates, data quality, masses
that are still estimates, SIL fault-suite results - and gives a GO / NO-GO
with the reason for every item. Thresholds come from the mission's Limits,
which the user must set from their safety code and range requirements.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from aerodyne.core.provenance import DataKind, DataQuality
from aerodyne.dynamics.simulator import SimulationResult
from aerodyne.environment.atmosphere import G0
from aerodyne.propulsion.motor import MotorPerformance
from aerodyne.recovery.recovery import descent_rate
from aerodyne.structures.flutter import flutter_along_trajectory
from aerodyne.vehicle.components import FinSet
from aerodyne.vehicle.mass import MassPropertiesEngine
from aerodyne.workspace.design import Design
from aerodyne.workspace.mission import Limits


@dataclass(frozen=True)
class Check:
    id: str
    name: str
    status: str            # PASS | WARN | FAIL | INFO | NOT RUN
    value: str
    requirement: str
    critical: bool
    evidence: str          # where the number came from (SIMULATED / ESTIMATED / ...)
    action: str = ""


def _fmt(v, nd=1):
    return "—" if v is None else f"{v:.{nd}f}"


def review(design: Design, motor: MotorPerformance, limits: Limits, nominal: SimulationResult,
           monte_carlo: dict | None = None, sil: dict | None = None, atmosphere=None) -> dict:
    checks: list[Check] = []
    add = checks.append
    v = design.vehicle
    s = nominal.summary()

    problems = v.validate()
    add(Check("geometry", "Vehicle geometry valid", "FAIL" if problems else "PASS",
              "; ".join(problems) or "no problems", "nose, fins, motor slot present; positive masses",
              True, "design"))

    q = motor.metadata.data_quality
    ok_q = q in (DataQuality.CERTIFIED, DataQuality.MANUFACTURER, DataQuality.MEASURED)
    add(Check("motor_data", "Motor data quality", "PASS" if ok_q else "FAIL",
              f"{motor.metadata.designation}: {q.value}", "CERTIFIED, MANUFACTURER or MEASURED",
              True, motor.metadata.source,
              "" if ok_q else "import the certified thrust curve for the motor you will fly"))

    # stability
    t_rail = nominal.event_time("rail_exit") or 0.0
    m_rail = float(np.interp(t_rail, nominal.t, nominal.stability_margin))
    m_min = s["min_stability_margin_cal"]
    add(Check("margin_rail", "Static margin at rail exit",
              "PASS" if m_rail >= limits.min_margin_cal else "FAIL", f"{m_rail:.2f} cal",
              f"≥ {limits.min_margin_cal:.2f} cal", True,
              "ESTIMATED (Barrowman). Cross-check: AERODYNE's CP averaged ~6 % aft of OpenRocket's "
              "on its example designs - verify marginal designs with a second tool",
              "" if m_rail >= limits.min_margin_cal else "add nose weight or enlarge/move fins aft"))
    if m_min is not None:
        st = ("FAIL" if m_min < limits.min_margin_cal else
              "WARN" if m_min > limits.max_margin_cal else "PASS")
        add(Check("margin_min", "Minimum static margin in powered flight", st, f"{m_min:.2f} cal",
                  f"{limits.min_margin_cal:.1f}–{limits.max_margin_cal:.1f} cal", True,
                  "SIMULATED", "over-stable designs weathercock strongly into wind" if st == "WARN" else ""))
    rv = s["rail_exit_velocity_mps"]
    add(Check("rail_exit", "Rail exit velocity",
              "PASS" if rv is not None and rv >= limits.min_rail_exit_mps else "FAIL", f"{_fmt(rv)} m/s",
              f"≥ {limits.min_rail_exit_mps:.1f} m/s", True, "SIMULATED",
              "" if rv and rv >= limits.min_rail_exit_mps else "longer rail or higher-thrust motor"))

    liftoff_mass = MassPropertiesEngine(v).configuration("FULL", motor).mass
    t_early = np.linspace(0, min(0.5, motor.burn_time), 26)
    tw = float(np.mean([motor.thrust_at(x) for x in t_early])) / (liftoff_mass * G0)
    add(Check("thrust_to_weight", "Thrust-to-weight (first 0.5 s)",
              "PASS" if tw >= limits.min_thrust_to_weight else "FAIL", f"{tw:.1f} : 1",
              f"≥ {limits.min_thrust_to_weight:.1f} : 1", True,
              f"motor curve ({q.value}) + mass ({MassPropertiesEngine(v).dry().kind.value})"))

    mach = s["max_mach"]
    add(Check("mach", "Aerodynamic model validity", "PASS" if mach <= limits.max_mach_analytical else "WARN",
              f"max Mach {mach:.2f}", f"≤ {limits.max_mach_analytical:.2f} for the analytical model",
              False, "SIMULATED",
              "" if mach <= limits.max_mach_analytical else
              "import RASAero/CFD aero tables and compare models before trusting the prediction"))

    # fin flutter along the flight
    if atmosphere is not None:
        for fs in [c for c in v.components if isinstance(c, FinSet)]:
            fl = flutter_along_trajectory(fs, nominal, atmosphere)
            if fl is None:
                add(Check(f"flutter_{fs.name}", f"Fin flutter ({fs.name})", "NOT RUN", "—",
                          "flutter speed ≥ 1.5× flight speed", False, "no shear modulus for this material",
                          "enter the fin laminate's shear modulus on the fin set"))
                continue
            r = fl["min_ratio"]
            add(Check(f"flutter_{fs.name}", f"Fin flutter ({fs.name})",
                      "PASS" if r >= 1.5 else "WARN" if r >= 1.0 else "FAIL",
                      f"{r:.2f}× (flutter {fl['flutter_speed_mps']:.0f} m/s vs {fl['speed_mps']:.0f} m/s at "
                      f"{fl['altitude_agl_m']:.0f} m AGL)", "flutter speed ≥ 1.5× flight speed", r < 1.0,
                      f"ESTIMATED, NACA TN 4197; G = {fl['shear_modulus_gpa']:.2f} GPa ({fl['shear_modulus_source']})",
                      "" if r >= 1.5 else "thicker or stiffer fins, smaller span, or a lower-speed flight"))

    # altitude and dispersion - prefer Monte Carlo percentiles
    mc_stats = (monte_carlo or {}).get("statistics", {})
    apo = mc_stats.get("apogee_agl_m", {}).get("p95") if mc_stats else None
    apo_src = "SIMULATED, Monte Carlo P95" if apo is not None else "SIMULATED, nominal only"
    apo = apo if apo is not None else s["apogee_agl_m"]
    if limits.altitude_ceiling_agl_m:
        add(Check("ceiling", "Apogee under altitude ceiling",
                  "PASS" if apo <= limits.altitude_ceiling_agl_m else "FAIL", f"{apo:.0f} m AGL",
                  f"≤ {limits.altitude_ceiling_agl_m:.0f} m AGL", True, apo_src))
    else:
        add(Check("ceiling", "Apogee under altitude ceiling", "NOT RUN", f"{apo:.0f} m AGL",
                  "set the waiver / range ceiling in the mission limits", False, apo_src))
    disp = (monte_carlo or {}).get("landing_dispersion")
    if limits.field_radius_m and disp:
        add(Check("dispersion", "Landing within recovery field",
                  "PASS" if disp["max_range_m"] <= limits.field_radius_m else "FAIL",
                  f"farthest MC landing {disp['max_range_m']:.0f} m", f"≤ {limits.field_radius_m:.0f} m",
                  True, "SIMULATED, Monte Carlo"))
    else:
        rng = float(np.hypot(s["landing_east_m"] or 0, s["landing_north_m"] or 0))
        add(Check("dispersion", "Landing within recovery field", "NOT RUN",
                  f"nominal landing {rng:.0f} m from pad",
                  "run Monte Carlo and set the field radius" if not disp else "set the field radius",
                  False, "SIMULATED, nominal"))

    # recovery
    rc = design.recovery
    if rc is None:
        add(Check("recovery", "Recovery system defined", "FAIL", "none", "at least one device", True,
                  "design"))
    else:
        spent = MassPropertiesEngine(v).configuration("MOTOR_SPENT", motor).mass
        rho = 1.2
        drogues = [d for d in rc.devices if d.deploy_event == "apogee"]
        mains = [d for d in rc.devices if d.deploy_event == "altitude"]
        if drogues and mains:
            vd = descent_rate(spent, rc.body_cd_area + sum(d.cd_area for d in drogues), rho)
            add(Check("drogue_rate", "Drogue descent rate", "PASS" if vd <= limits.max_drogue_rate_mps else "WARN",
                      f"{vd:.1f} m/s", f"≤ {limits.max_drogue_rate_mps:.0f} m/s", False, "ESTIMATED"))
        vm = descent_rate(spent, rc.body_cd_area + sum(d.cd_area for d in rc.devices), rho)
        ok = limits.main_rate_min_mps <= vm <= limits.main_rate_max_mps
        add(Check("landing_rate", "Landing descent rate", "PASS" if ok else "FAIL", f"{vm:.1f} m/s",
                  f"{limits.main_rate_min_mps:.1f}–{limits.main_rate_max_mps:.1f} m/s", True,
                  "ESTIMATED (sea-level density)",
                  "" if ok else ("larger main canopy" if vm > limits.main_rate_max_mps else
                                 "smaller main canopy (drift)")))
        add(Check("landing_energy", "Landing kinetic energy (whole vehicle)", "INFO",
                  f"{0.5 * spent * vm * vm:.0f} J", "check per-section limits of your safety code",
                  False, "ESTIMATED"))
        dep = [(t, n) for t, n in nominal.events if n.startswith("deploy:")]
        for t, n in dep:
            spd = float(np.interp(t, nominal.t, np.linalg.norm(nominal.velocity, axis=1)))
            add(Check(f"deploy_{n[7:]}", f"Speed at {n[7:]} deployment", "INFO", f"{spd:.1f} m/s",
                      "inform shock-cord / canopy rating", False, "SIMULATED"))

    # masses measured vs estimated
    comps = [c for c in v.components if c is not v.motor_slot]
    meas = sum(1 for c in comps if c.mass_kind == DataKind.MEASURED)
    add(Check("masses", "Component masses measured", "PASS" if meas == len(comps) else "WARN",
              f"{meas} of {len(comps)} weighed", "all components weighed before final prediction",
              False, "design", "" if meas == len(comps) else "weigh parts and enter mass overrides"))

    if sil is None:
        add(Check("sil", "Flight software fault suite (SIL)", "NOT RUN", "—",
                  "all scenarios pass", True, "run the SIL suite for this mission"))
    else:
        failed = [r["scenario"] for r in sil["results"] if r["issues"]]
        add(Check("sil", "Flight software fault suite (SIL)", "FAIL" if failed else "PASS",
                  f"{len(sil['results']) - len(failed)}/{len(sil['results'])} scenarios pass"
                  + (f" (failed: {', '.join(failed)})" if failed else ""),
                  "all scenarios pass", True, f"SIL backend {sil.get('backend')}"))

    crit_fail = [c for c in checks if c.critical and c.status in ("FAIL", "NOT RUN")]
    warn = [c for c in checks if c.status in ("WARN", "FAIL", "NOT RUN")]
    status = "NO-GO" if crit_fail else ("GO WITH WARNINGS" if warn else "GO")
    return {"status": status, "checks": [asdict(c) for c in checks],
            "blocking": [c.name for c in crit_fail],
            "note": "Readiness is an engineering aid. Final go/no-go is the RSO's under your safety code."}
