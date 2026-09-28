"""Model error analysis.

Reports POSSIBLE CONTRIBUTORS with the supporting evidence. It never assigns
causality: several contributors usually fit the same symptom, and separating
them needs dedicated tests (mass measurement, static fire, wind data ...).
"""

from __future__ import annotations

from dataclasses import dataclass

G = 9.80665


@dataclass(frozen=True)
class PossibleContributor:
    name: str
    evidence: str
    consistent_with_data: bool
    suggested_check: str


def _rel(a, b):
    if a is None or b is None or not b:
        return None
    return (a - b) / abs(b)


def coast_efficiency(summary: dict) -> float | None:
    """Fraction of ballistic (vacuum) coast height actually achieved:
    (apogee - h_burnout) / (v_burnout^2 / 2g). Lower = more drag."""
    v, hb, ap = (summary.get("burnout_velocity_mps"), summary.get("burnout_altitude_agl_m"),
                 summary.get("apogee_agl_m"))
    if not v or hb is None or ap is None:
        return None
    return (ap - hb) / (v * v / (2 * G))


def diagnose(sim: dict, actual: dict, tolerance: float = 0.03,
             environment: dict | None = None) -> list[PossibleContributor]:
    """``sim`` and ``actual`` are summary dicts. Must contain burnout velocity /
    altitude for the phase split; missing data reduces what can be said."""
    out: list[PossibleContributor] = []
    d_apo = _rel(sim.get("apogee_agl_m"), actual.get("apogee_agl_m"))
    d_vbo = _rel(sim.get("burnout_velocity_mps"), actual.get("burnout_velocity_mps"))
    d_tbo = _rel(sim.get("burnout_time_s"), actual.get("burnout_time_s"))
    d_acc = _rel(sim.get("max_axial_accel_mps2"), actual.get("max_axial_accel_mps2"))
    eff_s, eff_a = coast_efficiency(sim), coast_efficiency(actual)

    if d_apo is not None and abs(d_apo) <= tolerance:
        out.append(PossibleContributor(
            "none significant", f"apogee within {100 * tolerance:.0f}% ({100 * d_apo:+.1f}%)",
            True, "keep collecting flights; agreement on one flight is not validation"))

    if d_vbo is not None and abs(d_vbo) > tolerance:
        out.append(PossibleContributor(
            "motor-performance difference",
            f"burnout velocity differs by {100 * d_vbo:+.1f}% (boost phase)", True,
            "compare against a static-test or certification curve; check motor temperature"))
        out.append(PossibleContributor(
            "mass error", f"boost-phase velocity differs by {100 * d_vbo:+.1f}%; "
            f"peak acceleration differs by {100 * (d_acc or 0):+.1f}%", True,
            "weigh the loaded vehicle and motor; re-measure CG"))
    if d_tbo is not None and abs(d_tbo) > 0.05:
        out.append(PossibleContributor(
            "motor-performance difference", f"burn duration differs by {100 * d_tbo:+.1f}%", True,
            "burn time is a motor property largely independent of vehicle drag"))
    if eff_s is not None and eff_a is not None and abs(eff_s - eff_a) > 0.02:
        more = "more" if eff_a < eff_s else "less"
        out.append(PossibleContributor(
            "drag-model error", f"coast efficiency simulated {eff_s:.3f} vs actual {eff_a:.3f} "
            f"({more} coast drag than modelled)", True,
            "surface finish, fin alignment, rail buttons; compare Cd with RASAero/OpenRocket/CFD"))
        out.append(PossibleContributor(
            "aerodynamic coefficient error",
            "coast deviation can also come from normal-force / CP error (weathercocking losses)",
            True, "check stability margin and flight attitude from gyro data"))
        out.append(PossibleContributor(
            "wind", "wind-induced angle of attack increases coast losses and tilts the trajectory",
            True, "compare with measured winds aloft at launch time"))
    le_s, le_a = sim.get("landing_east_m"), actual.get("landing_east_m")
    ln_s, ln_a = sim.get("landing_north_m"), actual.get("landing_north_m")
    if None not in (le_s, le_a, ln_s, ln_a):
        miss = ((le_s - le_a) ** 2 + (ln_s - ln_a) ** 2) ** 0.5
        if miss > 100:
            out.append(PossibleContributor(
                "wind", f"landing point differs by {miss:.0f} m", True,
                "use measured wind profile; check recovery deployment altitudes"))
    if environment:
        dt = environment.get("temperature_offset_k")
        if dt is not None and abs(dt) > 5:
            out.append(PossibleContributor(
                "atmospheric model", f"launch-site temperature deviated {dt:+.1f} K from the "
                "model assumption", True, "re-run the simulation with the measured profile"))
        s = environment.get("baro_vs_gnss_apogee_m")
        if s is not None and abs(s) > 15:
            out.append(PossibleContributor(
                "sensor error", f"baro and GNSS apogee disagree by {s:.0f} m", True,
                "check baro port sizing / vent holes and GNSS dynamic-model settings"))
    if d_apo is not None and abs(d_apo) > tolerance and len(out) == 0:
        out.append(PossibleContributor(
            "launch conditions", f"apogee differs by {100 * d_apo:+.1f}% without a clear boost or "
            "coast signature", True, "check rail angle, rail friction, launch-site altitude"))
    # de-duplicate by name, keeping all evidence
    merged: dict[str, PossibleContributor] = {}
    for c in out:
        if c.name in merged:
            m = merged[c.name]
            merged[c.name] = PossibleContributor(c.name, m.evidence + "; " + c.evidence,
                                                 True, m.suggested_check)
        else:
            merged[c.name] = c
    return list(merged.values())


def render(contributors: list[PossibleContributor]) -> str:
    lines = ["POSSIBLE CONTRIBUTORS (not causal attributions):"]
    for c in contributors:
        lines.append(f"  • {c.name}\n      evidence: {c.evidence}\n      check:    {c.suggested_check}")
    return "\n".join(lines)
