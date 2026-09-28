"""Measured thrust-data analysis (performance characterization of static tests).

Input: raw load-cell samples of a lawfully and safely conducted test of a
(typically commercial/certified) motor. Output: burn duration, peak/average
thrust, total impulse, thrust and impulse curves, and a propagated measurement
uncertainty. Raw data is never modified; all products are DERIVED.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from aerodyne.core.provenance import DataKind, DataQuality
from aerodyne.propulsion.motor import MotorMetadata, MotorPerformance, _trapz


@dataclass(frozen=True)
class MotorAnalysisResult:
    raw_time: np.ndarray
    raw_force: np.ndarray
    baseline: float                 # N, tare offset removed
    noise_sigma: float              # N, 1-sigma from pre-ignition samples
    ignition_time: float            # s in raw timebase
    burnout_time: float             # s in raw timebase
    time: np.ndarray                # s, relative to ignition
    thrust: np.ndarray              # N, tare-corrected
    impulse_curve: np.ndarray       # Ns
    total_impulse: float
    total_impulse_sigma: float
    peak_thrust: float
    average_thrust: float
    burn_time: float
    threshold_fraction: float
    kind: DataKind = DataKind.DERIVED
    warnings: list[str] = field(default_factory=list)

    def to_motor(self, manufacturer: str, designation: str, total_mass: float,
                 propellant_mass: float | None, source: str, source_date: str,
                 certification: str | None = None) -> MotorPerformance:
        meta = MotorMetadata(manufacturer=manufacturer, designation=designation, source=source,
                             source_date=source_date, data_quality=DataQuality.MEASURED,
                             certification=certification,
                             notes=f"static test; I = {self.total_impulse:.1f} ± "
                                   f"{self.total_impulse_sigma:.1f} Ns (1σ)")
        return MotorPerformance(self.time, np.clip(self.thrust, 0, None), total_mass=total_mass,
                                propellant_mass=propellant_mass, metadata=meta,
                                thrust_uncertainty_rel=self.total_impulse_sigma / self.total_impulse)

    def summary(self) -> dict[str, float | str]:
        return {
            "total_impulse_Ns": round(self.total_impulse, 2),
            "total_impulse_sigma_Ns": round(self.total_impulse_sigma, 2),
            "peak_thrust_N": round(self.peak_thrust, 2),
            "average_thrust_N": round(self.average_thrust, 2),
            "burn_time_s": round(self.burn_time, 4),
            "baseline_N": round(self.baseline, 3),
            "noise_sigma_N": round(self.noise_sigma, 3),
            "kind": self.kind.value,
        }


def analyze_thrust_data(time, force, threshold_fraction: float = 0.05,
                        calibration_uncertainty_rel: float = 0.01,
                        baseline_samples: int | None = None) -> MotorAnalysisResult:
    """Analyze raw thrust samples.

    ``threshold_fraction`` defines burn start/end as the first/last sample above
    that fraction of peak thrust (5 % is a common convention).
    ``calibration_uncertainty_rel`` is the 1-sigma relative load-cell calibration
    uncertainty (systematic).
    """
    t = np.asarray(time, dtype=float).copy()
    f = np.asarray(force, dtype=float).copy()
    warnings: list[str] = []
    if t.shape != f.shape or t.ndim != 1 or len(t) < 10:
        raise ValueError("need equal-length 1-D time/force arrays with >= 10 samples")
    if np.any(np.diff(t) <= 0):
        raise ValueError("time must be strictly increasing")
    if np.any(~np.isfinite(f)):
        raise ValueError("force contains non-finite samples; clean or flag them first")

    rough_peak = float(np.max(f))
    above = np.nonzero(f > threshold_fraction * rough_peak)[0]
    if len(above) == 0:
        raise ValueError("no burn detected")
    first = int(above[0])
    nb = baseline_samples if baseline_samples is not None else max(0, first - 2)
    if nb >= 5:
        base_seg = f[:nb]
        baseline = float(np.median(base_seg))
        sigma = float(np.std(base_seg, ddof=1))
    else:
        baseline, sigma = 0.0, 0.0
        warnings.append("fewer than 5 pre-ignition samples; tare and noise not estimated")

    fc = f - baseline
    peak = float(np.max(fc))
    idx = np.nonzero(fc >= threshold_fraction * peak)[0]
    i0, i1 = int(idx[0]), int(idx[-1])
    # include the neighbouring samples so the integral captures the rise/tail-off
    j0, j1 = max(i0 - 1, 0), min(i1 + 1, len(t) - 1)
    tw = t[j0:j1 + 1]
    fw = np.clip(fc[j0:j1 + 1], 0.0, None)
    rel_t = tw - tw[0]
    impulse_curve = np.concatenate([[0.0], np.cumsum(0.5 * (fw[1:] + fw[:-1]) * np.diff(tw))])
    total = float(impulse_curve[-1])
    burn = float(t[i1] - t[i0])

    dt = float(np.median(np.diff(tw)))
    n = len(tw)
    s_cal = calibration_uncertainty_rel * total
    s_noise = sigma * math.sqrt(n) * dt
    s_base = (sigma / math.sqrt(nb) if nb >= 5 else 0.0) * (tw[-1] - tw[0])
    s_total = math.sqrt(s_cal ** 2 + s_noise ** 2 + s_base ** 2)
    if np.max(np.diff(t)) > 5 * np.min(np.diff(t)):
        warnings.append("irregular sample spacing detected")
    if peak > 0 and dt > 0.05 * burn:
        warnings.append("sample rate is low relative to burn time; impulse may be biased")

    return MotorAnalysisResult(
        raw_time=np.asarray(time, dtype=float), raw_force=np.asarray(force, dtype=float),
        baseline=baseline, noise_sigma=sigma, ignition_time=float(t[i0]),
        burnout_time=float(t[i1]), time=rel_t, thrust=fc[j0:j1 + 1], impulse_curve=impulse_curve,
        total_impulse=total, total_impulse_sigma=s_total, peak_thrust=peak,
        average_thrust=total / burn if burn > 0 else float("nan"), burn_time=burn,
        threshold_fraction=threshold_fraction, warnings=warnings)


__all__ = ["MotorAnalysisResult", "analyze_thrust_data", "_trapz"]
