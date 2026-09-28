"""Generic test-stand data ingestion (force, temperature, pressure, time).

Produces raw data (untouched), filtered data (zero-phase low-pass), derived
values, per-channel uncertainty and an optional plot. For propulsion tests the
force channel is handed to :func:`aerodyne.propulsion.analyze_thrust_data` for
performance characterization.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import signal

from aerodyne.core.provenance import DataKind


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    units: str
    calibration_uncertainty_rel: float = 0.01
    cutoff_hz: float | None = None      # low-pass cutoff; None = no filtering


@dataclass
class TestChannelResult:
    spec: ChannelSpec
    raw: np.ndarray
    filtered: np.ndarray
    noise_sigma: float
    stats: dict[str, float]
    kind_raw: DataKind = DataKind.MEASURED
    kind_filtered: DataKind = DataKind.DERIVED


@dataclass
class TestDataResult:
    test_id: str
    time: np.ndarray
    sample_rate: float
    channels: dict[str, TestChannelResult]
    derived: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def report(self) -> dict[str, object]:
        return {
            "test_id": self.test_id, "samples": int(len(self.time)),
            "sample_rate_hz": round(self.sample_rate, 2),
            "channels": {n: {"units": c.spec.units, "noise_sigma": round(c.noise_sigma, 5),
                             **{k: round(v, 5) for k, v in c.stats.items()}}
                         for n, c in self.channels.items()},
            "derived": self.derived, "warnings": self.warnings,
        }

    def plot(self, path: str | Path) -> Path | None:
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            return None
        fig, axes = plt.subplots(len(self.channels), 1, figsize=(8, 2.6 * len(self.channels)),
                                 sharex=True, squeeze=False)
        for ax, (n, c) in zip(axes[:, 0], self.channels.items()):
            ax.plot(self.time, c.raw, lw=0.6, alpha=0.5, label="raw (MEASURED)")
            ax.plot(self.time, c.filtered, lw=1.2, label="filtered (DERIVED)")
            ax.set_ylabel(f"{n} [{c.spec.units}]")
            ax.legend(loc="upper right", fontsize=7)
        axes[-1, 0].set_xlabel("time [s]")
        fig.suptitle(f"Test {self.test_id}")
        fig.tight_layout()
        fig.savefig(path, dpi=120)
        plt.close(fig)
        return Path(path)


def analyze_test_data(time, channels: dict[str, np.ndarray], specs: dict[str, ChannelSpec],
                      test_id: str, quiet_until: float | None = None) -> TestDataResult:
    t = np.asarray(time, dtype=float)
    warnings = []
    if np.any(np.diff(t) <= 0):
        raise ValueError("time must be strictly increasing")
    dt = np.diff(t)
    fs = 1.0 / float(np.median(dt))
    if dt.max() > 3 * dt.min():
        warnings.append("irregular sampling; filtering assumes the median rate")
    quiet = t <= (quiet_until if quiet_until is not None else t[0] + 0.05 * (t[-1] - t[0]))
    out = {}
    for name, raw in channels.items():
        spec = specs.get(name, ChannelSpec(name, ""))
        x = np.asarray(raw, dtype=float)
        if np.any(~np.isfinite(x)):
            warnings.append(f"{name}: {int(np.sum(~np.isfinite(x)))} non-finite samples interpolated")
            good = np.isfinite(x)
            x = np.interp(t, t[good], x[good])
        filt = x
        if spec.cutoff_hz and spec.cutoff_hz < fs / 2:
            b, a = signal.butter(4, spec.cutoff_hz / (fs / 2))
            if len(x) > 3 * max(len(a), len(b)):
                filt = signal.filtfilt(b, a, x)
        sigma = float(np.std(x[quiet], ddof=1)) if quiet.sum() > 5 else float("nan")
        peak = float(np.max(filt))
        out[name] = TestChannelResult(spec, np.asarray(raw, dtype=float), filt, sigma, {
            "min": float(np.min(filt)), "max": peak, "mean": float(np.mean(filt)),
            "peak_uncertainty": math.hypot(spec.calibration_uncertainty_rel * abs(peak),
                                           sigma if math.isfinite(sigma) else 0.0)})
    return TestDataResult(test_id, t, fs, out, warnings=warnings)


def read_test_csv(path: str | Path, time_col: str = "time") -> tuple[np.ndarray, dict[str, np.ndarray]]:
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    cols = [c for c in rows[0] if c != time_col]
    t = np.array([float(r[time_col]) for r in rows])
    return t, {c: np.array([float(r[c]) if r[c] != "" else np.nan for r in rows]) for c in cols}
