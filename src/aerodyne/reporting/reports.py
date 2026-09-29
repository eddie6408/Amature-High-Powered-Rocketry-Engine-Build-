"""Markdown report generation. Every value table states its data kind."""

from __future__ import annotations

from aerodyne.analysis import comparison as cmp
from aerodyne.analysis.model_error import PossibleContributor
from aerodyne.dynamics.simulator import SimulationResult
from aerodyne.montecarlo.engine import MonteCarloResult
from aerodyne.propulsion.motor import MotorPerformance


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def simulation_report(res: SimulationResult, title: str = "Simulation") -> str:
    s = res.summary()
    lines = [f"# {title}", "", f"Data kind: **{res.kind.value}**", "", "| Metric | Value |",
             "|---|---|"]
    lines += [f"| {k} | {_fmt(v)} |" for k, v in s.items() if k != "kind"]
    lines += ["", "## Events", "", "| t [s] | event |", "|---|---|"]
    lines += [f"| {t:.2f} | {n} |" for t, n in res.events]
    if res.notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in res.notes]
    return "\n".join(lines) + "\n"


def monte_carlo_report(mc: MonteCarloResult, title: str = "Monte Carlo dispersion") -> str:
    rep = mc.report()
    lines = [f"# {title}", "", f"Runs: {rep['runs']} (failures: {rep['failures']}), seed "
             f"{rep['seed']}. Data kind: **{rep['kind']}**", "",
             "| Output | mean | σ | P5 | P50 | P95 |", "|---|---|---|---|---|---|"]
    for k, st in rep["statistics"].items():
        if st:
            lines.append(f"| {k} | {_fmt(st['mean'])} | {_fmt(st['std'])} | {_fmt(st['p05'])} | "
                         f"{_fmt(st['p50'])} | {_fmt(st['p95'])} |")
    ld = rep["landing_dispersion"]
    if ld:
        lines += ["", "## Landing dispersion (95 % ellipse)", "",
                  f"Centre: {ld['mean_east_m']:.0f} m E, {ld['mean_north_m']:.0f} m N; semi-axes "
                  f"{ld['semi_major_m']:.0f} × {ld['semi_minor_m']:.0f} m, major-axis bearing "
                  f"{ld['major_axis_bearing_deg']:.0f}°; farthest run {ld['max_range_m']:.0f} m."]
    if mc.failures:
        lines += ["", "## Failed runs", ""] + [f"- run {i}: {e}" for i, e in mc.failures]
    return "\n".join(lines) + "\n"


def comparison_report(rows: list[cmp.ComparisonRow], contributors: list[PossibleContributor],
                      actual_kind: str) -> str:
    lines = ["# Simulation vs reality", "",
             f"Actual data kind: **{actual_kind}**", "",
             "| Metric | Simulated | Actual | Abs. error | % error |", "|---|---|---|---|---|"]
    for r in rows:
        pct = "—" if r.pct_error is None else f"{r.pct_error:+.1f} %"
        lines.append(f"| {r.label} [{r.units}] | {_fmt(r.simulated)} | {_fmt(r.actual)} | "
                     f"{_fmt(r.abs_error)} | {pct} |")
    lines += ["", "## Possible contributors", "",
              "_Evidence-based candidates, not causal attributions._", ""]
    for c in contributors:
        lines.append(f"- **{c.name}** — {c.evidence}. _Check:_ {c.suggested_check}")
    return "\n".join(lines) + "\n"


def motor_report(m: MotorPerformance) -> str:
    s = m.summary()
    lines = [f"# Motor {s['designation']}", "", "| Field | Value |", "|---|---|"]
    lines += [f"| {k} | {_fmt(v)} |" for k, v in s.items()]
    return "\n".join(lines) + "\n"


def flight_report(flight: dict, analysis: dict | None) -> str:
    """Complete flight record as Markdown: configuration, raw-data integrity,
    reconstruction vs prediction, possible contributors, provenance."""
    c = flight["configuration"]
    m = flight.get("motor", {})
    lines = [f"# Flight report {flight['flight_id']}", "",
             f"Date: {flight.get('date') or '—'} · Vehicle {flight['vehicle_id']} {flight['revision']} · "
             f"Motor {m.get('manufacturer', '')} {m.get('designation', '')} ({m.get('data_quality', '?')})", "",
             "## Flown configuration", "", "| Item | Value |", "|---|---|"]
    lines += [f"| {k} | {_fmt(v) if v != '' else '—'} |" for k, v in c.items()]
    lines += [f"| configuration hash | `{flight.get('configuration_hash', '')}` |", "",
              "## Raw data (MEASURED, write-once)", "", "| File | Bytes | SHA-256 | Source |", "|---|---|---|---|"]
    lines += [f"| {r['name']} | {r['bytes']} | `{r['sha256']}` | {r.get('source', '')} |"
              for r in flight.get("raw_files", [])]
    if not analysis:
        lines += ["", "_Not analysed yet._"]
        return "\n".join(lines) + "\n"
    lines += ["", "## Simulation vs reality", "",
              f"Actual data: **{analysis['actual_kind']}** from `{analysis['file']}`; prediction: "
              f"{analysis.get('prediction_basis', '')}; digital twin: **{analysis['status']}**", "",
              "| Metric | Simulated | Actual | Abs. error | % error |", "|---|---|---|---|---|"]
    for r in analysis["comparison"]:
        pct = "—" if r["pct_error"] is None else f"{r['pct_error']:+.1f} %"
        lines.append(f"| {r['label']} [{r['units']}] | {_fmt(r['simulated'])} | {_fmt(r['actual'])} | "
                     f"{_fmt(r['abs_error'])} | {pct} |")
    lines += ["", "## Flight phases (reconstructed)", ""]
    lines += [f"- {k}: {v:.2f} s" for k, v in (analysis.get("phases") or {}).items() if v is not None]
    lines += ["", "## Possible contributors", "", "_Evidence-based candidates, not causal attributions._", ""]
    lines += [f"- **{x['name']}**: {x['evidence']}. _Check:_ {x['suggested_check']}"
              for x in analysis.get("contributors", [])]
    if analysis.get("notes"):
        lines += ["", "## Notes", ""] + [f"- {n}" for n in analysis["notes"]]
    if analysis.get("motor_quality") not in ("CERTIFIED", "MANUFACTURER", "MEASURED"):
        lines += ["", f"> Prediction used {analysis.get('motor_quality')} motor data."]
    return "\n".join(lines) + "\n"
