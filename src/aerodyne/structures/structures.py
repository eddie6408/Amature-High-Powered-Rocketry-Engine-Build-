"""Structural cases and results.

AERODYNE does not replace FEA (Fusion Simulation, SolidWorks Simulation,
ANSYS ...). It derives *load cases* from simulation results, exports them for
the FEA tool, imports FEA results back into the project record, and computes
margins of safety against a design factor.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from aerodyne.core.provenance import DataKind
from aerodyne.dynamics.simulator import SimulationResult
from aerodyne.vehicle.vehicle import Vehicle


@dataclass(frozen=True)
class StructuralCase:
    name: str
    load_type: str                 # acceleration | aerodynamic | recovery | landing
    time: float | None
    loads: dict[str, float]        # e.g. {"axial_accel_g": 18, "axial_force_N": 300}
    kind: DataKind
    source: str


@dataclass(frozen=True)
class StructuralResult:
    case: str
    component: str
    max_stress_pa: float
    allowable_pa: float
    design_factor: float
    tool: str                      # e.g. "ANSYS 2025 R1", "Fusion Simulation"
    kind: DataKind = DataKind.SIMULATED

    @property
    def margin_of_safety(self) -> float:
        return margin_of_safety(self.allowable_pa, self.max_stress_pa, self.design_factor)


def margin_of_safety(allowable: float, applied: float, design_factor: float = 1.5) -> float:
    """MS = allowable / (applied * FS) - 1. MS < 0 fails."""
    if applied <= 0:
        return float("inf")
    return allowable / (applied * design_factor) - 1.0


def derive_load_cases(sim: SimulationResult, vehicle: Vehicle,
                      recovery_opening_loads: dict[str, float] | None = None,
                      landing_speed: float | None = None) -> list[StructuralCase]:
    """Envelope load cases from a simulation (SIMULATED)."""
    cases = []
    sf = sim.specific_force_body[:, 0]
    i = int(np.argmax(sf))
    cases.append(StructuralCase(
        "max axial acceleration", "acceleration", float(sim.t[i]),
        {"axial_accel_g": float(sf[i] / 9.80665), "mass_kg": float(sim.mass[i]),
         "axial_force_N": float(sf[i] * sim.mass[i])}, DataKind.SIMULATED, "flight-dynamics"))
    j = int(np.argmax(sim.dynamic_pressure))
    q = float(sim.dynamic_pressure[j])
    fins = vehicle.fin_sets()
    fin_area = fins[0].planform_area if fins else 0.0
    # fin normal load at max-q for an assumed gust-induced alpha (ESTIMATE)
    alpha_gust = max(float(np.max(sim.alpha)), np.radians(5.0))
    cases.append(StructuralCase(
        "max dynamic pressure", "aerodynamic", float(sim.t[j]),
        {"dynamic_pressure_pa": q, "mach": float(sim.mach[j]),
         "design_alpha_deg": float(np.degrees(alpha_gust)),
         "fin_normal_load_N_est": float(q * fin_area * 2 * np.pi * alpha_gust)},
        DataKind.SIMULATED, "flight-dynamics"))
    for name, load in (recovery_opening_loads or {}).items():
        cases.append(StructuralCase(f"{name} opening", "recovery", None,
                                    {"opening_load_N": load}, DataKind.ESTIMATED, "recovery"))
    if landing_speed is not None:
        cases.append(StructuralCase("landing", "landing", None,
                                    {"impact_speed_mps": landing_speed}, DataKind.SIMULATED,
                                    "recovery"))
    return cases


def import_fea_results(path: str | Path, tool: str, design_factor: float = 1.5
                       ) -> list[StructuralResult]:
    """CSV columns: case, component, max_stress_pa, allowable_pa."""
    with open(path, newline="") as fh:
        return [StructuralResult(r["case"], r["component"], float(r["max_stress_pa"]),
                                 float(r["allowable_pa"]), design_factor, tool)
                for r in csv.DictReader(fh)]
