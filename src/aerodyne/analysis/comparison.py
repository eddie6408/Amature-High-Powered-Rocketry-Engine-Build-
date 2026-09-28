"""Simulation vs reality."""

from __future__ import annotations

from dataclasses import dataclass

METRICS = [
    ("apogee_agl_m", "Apogee (AGL)", "m"),
    ("max_vertical_velocity_mps", "Max velocity (vert)", "m/s"),
    ("max_axial_accel_mps2", "Max axial accel", "m/s²"),
    ("burnout_velocity_mps", "Burnout velocity", "m/s"),
    ("time_to_apogee_s", "Time to apogee", "s"),
    ("burnout_time_s", "Burnout time", "s"),
    ("flight_time_s", "Flight duration", "s"),
    ("landing_east_m", "Landing east", "m"),
    ("landing_north_m", "Landing north", "m"),
]


@dataclass(frozen=True)
class ComparisonRow:
    key: str
    label: str
    units: str
    simulated: float | None
    actual: float | None

    @property
    def abs_error(self) -> float | None:
        if self.simulated is None or self.actual is None:
            return None
        return self.simulated - self.actual

    @property
    def pct_error(self) -> float | None:
        """Only meaningful for positive magnitudes; None for signed positions."""
        if self.abs_error is None or self.key.startswith("landing_") or not self.actual:
            return None
        return 100.0 * self.abs_error / abs(self.actual)


def compare(simulated: dict, actual: dict, metrics=METRICS) -> list[ComparisonRow]:
    return [ComparisonRow(k, label, u, simulated.get(k), actual.get(k)) for k, label, u in metrics]


def render(rows: list[ComparisonRow], sim_kind: str = "SIMULATED", actual_kind: str = "MEASURED") -> str:
    def f(x):
        return "—" if x is None else f"{x:10.2f}"

    head = f"{'':26s}{sim_kind:>14s}{actual_kind:>14s}{'abs err':>12s}{'% err':>9s}"
    lines = [head, "-" * len(head)]
    for r in rows:
        pct = "—" if r.pct_error is None else f"{r.pct_error:+.1f}%"
        lines.append(f"{r.label + ' [' + r.units + ']':26s}{f(r.simulated):>14s}{f(r.actual):>14s}"
                     f"{f(r.abs_error):>12s}{pct:>9s}")
    return "\n".join(lines)
