"""AERODYNE command-line interface.

    aerodyne stability                 mass properties + static margin of the example vehicle
    aerodyne simulate [--report F]     6-DOF simulation of the example vehicle
    aerodyne montecarlo -n 100         dispersion analysis
    aerodyne sil [--backend c] [--all-faults]
    aerodyne motor info FILE.eng [--quality CERTIFIED]
    aerodyne motor analyze FILE.csv    measured thrust data characterization
    aerodyne demo                      closed loop on SYNTHETIC flight data
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace

from aerodyne.core.provenance import DataKind, DataQuality


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_stability(args) -> int:
    from aerodyne.examples import example_motor, example_recovery, example_vehicle
    from aerodyne.twin import DigitalTwin

    twin = DigitalTwin("AERODYNE-EX1", "REV-A", example_vehicle(), example_motor(), example_recovery())
    for cfg in ("EMPTY", "FULL", "MOTOR_SPENT", "PAYLOAD_REMOVED"):
        mp = twin.mass_properties(cfg)
        print(f"{cfg:16s} mass {mp.mass:6.3f} kg  CG {mp.cg:6.3f} m  Iyy {mp.iyy:7.4f} kg·m²  [{mp.kind.value}]")
    st = twin.stability()
    print(f"\nCP {st['xcp_m']:.3f} m  CNα {st['cn_alpha']:.2f}/rad  "
          f"margin FULL {st['margin_full_cal']:.2f} cal, SPENT {st['margin_motor_spent_cal']:.2f} cal "
          "[ESTIMATED - Barrowman]")
    problems = twin.vehicle.validate()
    if problems:
        print("\nDESIGN WARNINGS: " + "; ".join(problems))
    return 0


def cmd_simulate(args) -> int:
    from aerodyne.dynamics.simulator import FlightSimulator
    from aerodyne.examples import example_config
    from aerodyne.reporting import simulation_report

    res = FlightSimulator(example_config()).run()
    print("NOTE: example vehicle with a HYPOTHETICAL synthetic motor.\n")
    _print_json(res.summary())
    if args.report:
        with open(args.report, "w") as fh:
            fh.write(simulation_report(res, "AERODYNE-EX1 nominal simulation"))
        print(f"report written to {args.report}")
    return 0


def cmd_montecarlo(args) -> int:
    from aerodyne.examples import example_config
    from aerodyne.montecarlo import MonteCarloEngine
    from aerodyne.reporting import monte_carlo_report

    eng = MonteCarloEngine(example_config())
    mc = eng.run(args.n, seed=args.seed, workers=args.workers,
                 progress=lambda i, n: print(f"\r  run {i}/{n}", end="", file=sys.stderr))
    print(file=sys.stderr)
    text = monte_carlo_report(mc)
    print(text)
    if args.report:
        with open(args.report, "w") as fh:
            fh.write(text)
    return 0 if not mc.failures else 1


def cmd_sil(args) -> int:
    from aerodyne.dynamics.simulator import FlightSimulator
    from aerodyne.examples import example_config
    from aerodyne.sil.faults import STANDARD_SCENARIOS
    from aerodyne.sil.runner import SILRunner

    sim = FlightSimulator(example_config()).run()
    names = list(STANDARD_SCENARIOS) if args.all_faults else [args.scenario]
    worst = 0
    for name in names:
        r = SILRunner(sim, STANDARD_SCENARIOS[name], backend=args.backend, seed=args.seed).run()
        issues = r.evaluate()
        worst |= bool(issues)
        seq = " → ".join(f"{s}@{t:.2f}s" for t, _, s, _ in r.transitions if t >= 0)
        print(f"[{'PASS' if not issues else 'FAIL'}] {name:24s} {seq}")
        print(f"         faults detected: {', '.join(r.faults_detected) or 'none'}; "
              f"link lost={r.link['lost']} crc={r.link['crc_failures']} dup={r.link['duplicates']}; "
              f"est apogee {r.est_apogee:.0f} m vs truth {r.true_apogee:.0f} m")
        for i in issues:
            print(f"         ISSUE: {i}")
    return int(worst)


def cmd_motor(args) -> int:
    from aerodyne.propulsion import analyze_thrust_data, read_eng, read_thrust_csv

    if args.action == "info":
        for m in read_eng(args.file, data_quality=DataQuality(args.quality)):
            _print_json(m.summary())
        return 0
    t, f = read_thrust_csv(args.file, args.time_col, args.force_col)
    res = analyze_thrust_data(t, f, threshold_fraction=args.threshold,
                              calibration_uncertainty_rel=args.cal_uncertainty)
    _print_json({**res.summary(), "warnings": res.warnings})
    return 0


def cmd_demo(args) -> int:
    """Closed loop DESIGN→SIMULATE→FLY→MEASURE→ANALYZE→COMPARE→IMPROVE, using a
    synthetic flight (the "truth" has 15 % more drag than the model)."""
    from aerodyne.aero.model import ScaledAeroModel
    from aerodyne.analysis import comparison, model_error
    from aerodyne.analysis.ingestion import logger_to_dataset
    from aerodyne.analysis.reconstruction import FlightReconstructionEngine
    from aerodyne.dynamics.simulator import FlightSimulator
    from aerodyne.examples import example_config, example_motor, example_recovery, example_vehicle
    from aerodyne.sil.runner import SILRunner
    from aerodyne.twin import DigitalTwin

    print("=== AERODYNE closed-loop demo (SYNTHETIC data - not a real flight) ===\n")
    base = example_config()
    twin = DigitalTwin("AERODYNE-EX1", "REV-A", example_vehicle(), example_motor(), example_recovery())
    predicted = FlightSimulator(base).run()
    print(f"1. predicted apogee {predicted.summary()['apogee_agl_m']:.0f} m [SIMULATED]")
    truth = FlightSimulator(replace(base, aero=ScaledAeroModel(base.aero, cd_scale=1.15))).run()
    sil = SILRunner(truth, [], backend=args.backend).run()
    print(f"2. synthetic flight flown through the flight software: "
          f"{' → '.join(s for t, _, s, _ in sil.transitions if t >= 0)}")
    ds = logger_to_dataset(sil.logger, "SYN-001", kind=DataKind.HYPOTHETICAL)
    rec = FlightReconstructionEngine(ds).run()
    actual = rec.summary()
    print(f"3. reconstructed apogee {actual['apogee_agl_m']:.0f} m [{rec.kind.value}]\n")
    rows = comparison.compare(predicted.summary(), actual)
    print(comparison.render(rows, actual_kind=rec.kind.value))
    print()
    print(model_error.render(model_error.diagnose(predicted.summary(), actual)))
    v = twin.validate_against_flight("SYN-001", predicted.summary(), actual)
    print(f"\n4. digital twin validation: {v.status}")
    prop = twin.propose_drag_calibration(actual["apogee_agl_m"], base)
    print(f"5. proposed Cd scale {prop.value:.3f} [{prop.kind.value}] - {prop.note} "
          f"(synthetic truth was 1.150)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aerodyne", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stability").set_defaults(fn=cmd_stability)
    s = sub.add_parser("simulate")
    s.add_argument("--report")
    s.set_defaults(fn=cmd_simulate)
    s = sub.add_parser("montecarlo")
    s.add_argument("-n", type=int, default=50)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--workers", type=int, default=1)
    s.add_argument("--report")
    s.set_defaults(fn=cmd_montecarlo)
    s = sub.add_parser("sil")
    s.add_argument("--backend", choices=["python", "c"], default="python")
    s.add_argument("--scenario", default="nominal")
    s.add_argument("--all-faults", action="store_true")
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(fn=cmd_sil)
    s = sub.add_parser("motor")
    s.add_argument("action", choices=["info", "analyze"])
    s.add_argument("file")
    s.add_argument("--quality", default="UNKNOWN", choices=[q.value for q in DataQuality])
    s.add_argument("--time-col", default="time")
    s.add_argument("--force-col", default="force")
    s.add_argument("--threshold", type=float, default=0.05)
    s.add_argument("--cal-uncertainty", type=float, default=0.01)
    s.set_defaults(fn=cmd_motor)
    s = sub.add_parser("demo")
    s.add_argument("--backend", choices=["python", "c"], default="python")
    s.set_defaults(fn=cmd_demo)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
