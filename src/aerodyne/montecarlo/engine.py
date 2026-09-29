"""Monte Carlo engine.

Uncertain inputs: dry mass and CG (manufacturing tolerance), motor impulse and
burn time, drag and normal-force coefficients, CP location, wind speed /
direction / gustiness, temperature, pressure, launch angle and azimuth.

Outputs are distributions (mean, sigma, percentiles) of apogee, maximum
velocity, maximum acceleration, flight time and landing position - never a
single number when uncertainty matters.
"""

from __future__ import annotations

import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np

from aerodyne.aero.model import ScaledAeroModel
from aerodyne.core.provenance import DataKind
from aerodyne.dynamics.simulator import FlightSimulator, SimulationConfig
from aerodyne.environment.atmosphere import StandardAtmosphere
from aerodyne.environment.wind import GustWind, PowerLawWind


class Distribution:
    def sample(self, rng: np.random.Generator) -> float:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass(frozen=True)
class Normal(Distribution):
    mean: float
    sigma: float

    def sample(self, rng: np.random.Generator) -> float:
        return float(rng.normal(self.mean, self.sigma)) if self.sigma > 0 else self.mean


@dataclass(frozen=True)
class Uniform(Distribution):
    low: float
    high: float

    def sample(self, rng: np.random.Generator) -> float:
        return float(rng.uniform(self.low, self.high))


@dataclass
class UncertaintyModel:
    dry_mass_scale: Distribution = field(default_factory=lambda: Normal(1.0, 0.02))
    dry_cg_shift: Distribution = field(default_factory=lambda: Normal(0.0, 0.01))       # m
    impulse_scale: Distribution = field(default_factory=lambda: Normal(1.0, 0.03))
    burn_time_scale: Distribution = field(default_factory=lambda: Normal(1.0, 0.03))
    cd_scale: Distribution = field(default_factory=lambda: Normal(1.0, 0.08))
    cn_alpha_scale: Distribution = field(default_factory=lambda: Normal(1.0, 0.05))
    xcp_shift: Distribution = field(default_factory=lambda: Normal(0.0, 0.01))          # m
    wind_speed: Distribution = field(default_factory=lambda: Uniform(0.0, 6.0))          # m/s at 10 m
    wind_from_deg: Distribution = field(default_factory=lambda: Uniform(0.0, 360.0))
    gust_sigma: Distribution = field(default_factory=lambda: Uniform(0.0, 1.5))
    temperature_offset: Distribution = field(default_factory=lambda: Normal(0.0, 5.0))  # K
    sea_level_pressure: Distribution = field(default_factory=lambda: Normal(101325.0, 500.0))
    elevation_deg: Distribution | None = None      # default: nominal +/- 1 deg
    azimuth_deg: Distribution | None = None
    # when set, winds are dispersed around this profile instead of a power law:
    # factory(speed_scale, direction_offset_deg) -> WindModel
    wind_factory: Callable[[float, float], object] | None = None
    wind_scale: Distribution = field(default_factory=lambda: Normal(1.0, 0.25))
    wind_dir_offset: Distribution = field(default_factory=lambda: Normal(0.0, 20.0))


@dataclass
class MonteCarloResult:
    samples: list[dict[str, float]]            # sampled inputs per run
    outputs: list[dict[str, float | None]]     # summary per run
    failures: list[tuple[int, str]]
    seed: int
    kind: DataKind = DataKind.SIMULATED

    def values(self, key: str) -> np.ndarray:
        return np.array([o[key] for o in self.outputs if o.get(key) is not None], dtype=float)

    def statistics(self, key: str) -> dict[str, float]:
        x = self.values(key)
        if len(x) == 0:
            return {}
        return {"n": int(len(x)), "mean": float(x.mean()), "std": float(x.std(ddof=1)) if len(x) > 1 else 0.0,
                "min": float(x.min()), "p05": float(np.percentile(x, 5)),
                "p50": float(np.percentile(x, 50)), "p95": float(np.percentile(x, 95)),
                "max": float(x.max())}

    def landing_dispersion(self, confidence: float = 0.95) -> dict[str, float] | None:
        e, n = self.values("landing_east_m"), self.values("landing_north_m")
        if len(e) < 3 or len(e) != len(n):
            return None
        pts = np.vstack([e, n])
        mean = pts.mean(axis=1)
        cov = np.cov(pts)
        evals, evecs = np.linalg.eigh(cov)
        k = math.sqrt(-2 * math.log(1 - confidence))   # chi-square 2 dof
        major = int(np.argmax(evals))
        return {"mean_east_m": float(mean[0]), "mean_north_m": float(mean[1]),
                "semi_major_m": float(k * math.sqrt(max(evals[major], 0))),
                "semi_minor_m": float(k * math.sqrt(max(evals[1 - major], 0))),
                "major_axis_bearing_deg": float(math.degrees(math.atan2(evecs[0, major],
                                                                        evecs[1, major])) % 180),
                "confidence": confidence,
                "max_range_m": float(np.max(np.hypot(e, n)))}

    def report(self) -> dict[str, object]:
        keys = ["apogee_agl_m", "max_velocity_mps", "max_acceleration_mps2", "max_mach",
                "time_to_apogee_s", "flight_time_s", "rail_exit_velocity_mps",
                "min_stability_margin_cal"]
        return {"runs": len(self.outputs), "failures": len(self.failures), "seed": self.seed,
                "statistics": {k: self.statistics(k) for k in keys},
                "landing_dispersion": self.landing_dispersion(), "kind": self.kind.value}


def _run_one(args: tuple[SimulationConfig, dict[str, float]]) -> dict[str, float | None]:
    cfg, _ = args
    return FlightSimulator(cfg).run().summary()


class MonteCarloEngine:
    def __init__(self, base: SimulationConfig, uncertainty: UncertaintyModel | None = None,
                 dt: float = 0.01) -> None:
        self.base = base
        self.unc = uncertainty or UncertaintyModel()
        self.dt = dt

    def sample_config(self, rng: np.random.Generator) -> tuple[SimulationConfig, dict[str, float]]:
        u, b = self.unc, self.base
        s = {
            "dry_mass_scale": u.dry_mass_scale.sample(rng),
            "dry_cg_shift": u.dry_cg_shift.sample(rng),
            "impulse_scale": u.impulse_scale.sample(rng),
            "burn_time_scale": u.burn_time_scale.sample(rng),
            "cd_scale": u.cd_scale.sample(rng),
            "cn_alpha_scale": u.cn_alpha_scale.sample(rng),
            "xcp_shift": u.xcp_shift.sample(rng),
            "wind_speed": max(0.0, u.wind_speed.sample(rng)),
            "wind_from_deg": u.wind_from_deg.sample(rng),
            "gust_sigma": max(0.0, u.gust_sigma.sample(rng)),
            "temperature_offset": u.temperature_offset.sample(rng),
            "sea_level_pressure": u.sea_level_pressure.sample(rng),
            "elevation_deg": (u.elevation_deg or Normal(b.site.elevation_deg, 1.0)).sample(rng),
            "azimuth_deg": (u.azimuth_deg or Normal(b.site.azimuth_deg, 2.0)).sample(rng),
            "gust_seed": int(rng.integers(0, 2 ** 31 - 1)),
            "wind_scale": max(0.0, u.wind_scale.sample(rng)),
            "wind_dir_offset": u.wind_dir_offset.sample(rng),
        }
        base_wind = (u.wind_factory(s["wind_scale"], s["wind_dir_offset"]) if u.wind_factory
                     else PowerLawWind(s["wind_speed"], s["wind_from_deg"]))
        s["elevation_deg"] = min(s["elevation_deg"], 90.0)
        cfg = replace(
            b,
            motor=b.motor.scaled(s["impulse_scale"], s["burn_time_scale"]),
            aero=ScaledAeroModel(b.aero, s["cd_scale"], s["cn_alpha_scale"], s["xcp_shift"]),
            atmosphere=StandardAtmosphere(s["temperature_offset"], s["sea_level_pressure"]),
            wind=GustWind(base_wind, s["gust_sigma"], seed=s["gust_seed"]),
            site=replace(b.site, elevation_deg=s["elevation_deg"], azimuth_deg=s["azimuth_deg"]),
            dry_mass_scale=s["dry_mass_scale"], dry_cg_shift=s["dry_cg_shift"], dt=self.dt,
        )
        return cfg, s

    def run(self, n: int, seed: int = 1, workers: int = 1,
            progress: Callable[[int, int], None] | None = None) -> MonteCarloResult:
        rng = np.random.default_rng(seed)
        jobs = [self.sample_config(rng) for _ in range(n)]
        outputs: list[dict[str, float | None]] = []
        samples: list[dict[str, float]] = []
        failures: list[tuple[int, str]] = []
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                futures = [ex.submit(_run_one, j) for j in jobs]
                for i, fut in enumerate(futures):
                    try:
                        outputs.append(fut.result())
                        samples.append(jobs[i][1])
                    except Exception as exc:  # record, never hide
                        failures.append((i, repr(exc)))
                    if progress:
                        progress(i + 1, n)
        else:
            for i, j in enumerate(jobs):
                try:
                    outputs.append(_run_one(j))
                    samples.append(j[1])
                except Exception as exc:
                    failures.append((i, repr(exc)))
                if progress:
                    progress(i + 1, n)
        return MonteCarloResult(samples=samples, outputs=outputs, failures=failures, seed=seed)
