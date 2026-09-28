"""Standardized motor-performance representation (TIME, THRUST + metadata).

The flight simulator consumes :class:`MotorPerformance` without caring whether
the curve came from a manufacturer, a certification database, a public test
dataset or a properly measured static test. The data quality is carried along
so it can be reported, never silently substituted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np

from aerodyne.core.provenance import DataQuality, stable_hash

_trapz = getattr(np, "trapezoid", None) or np.trapz  # numpy 1.x / 2.x


def impulse_class(total_impulse: float) -> str:
    """NAR/TRA letter class. A = 1.26-2.50 Ns, each letter doubles."""
    if total_impulse <= 0:
        raise ValueError("total impulse must be positive")
    if total_impulse <= 0.625:
        return "1/4A"
    if total_impulse <= 1.25:
        return "1/2A"
    idx = max(0, math.ceil(math.log2(total_impulse / 2.5) - 1e-12))
    letters = ""
    n = idx + 1
    while n > 0:  # beyond Z would be absurd, but stay well-defined
        n, rem = divmod(n - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


@dataclass(frozen=True)
class MotorMetadata:
    manufacturer: str
    designation: str
    source: str                         # where the data came from (URL, file, test ID)
    source_date: str                    # ISO date of the dataset
    data_quality: DataQuality
    diameter_mm: float | None = None
    length_mm: float | None = None
    certification: str | None = None    # certifying body / cert reference
    delays: str = ""
    notes: str = ""


@dataclass(frozen=True)
class MotorPerformance:
    time: np.ndarray                  # s, strictly increasing, starts at ignition (0)
    thrust: np.ndarray                # N
    total_mass: float                 # kg, loaded motor mass at ignition
    propellant_mass: float | None     # kg, as published; None if unknown
    metadata: MotorMetadata
    thrust_uncertainty_rel: float = 0.0   # 1-sigma relative thrust uncertainty if known
    _cum: np.ndarray = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        t = np.asarray(self.time, dtype=float)
        f = np.asarray(self.thrust, dtype=float)
        if t.ndim != 1 or t.shape != f.shape or len(t) < 2:
            raise ValueError("time and thrust must be equal-length 1-D arrays (>= 2 points)")
        if np.any(np.diff(t) <= 0):
            raise ValueError("time must be strictly increasing")
        if np.any(f < 0):
            raise ValueError("thrust must be non-negative")
        if t[0] > 0:  # anchor ignition at t=0 with zero thrust
            t = np.concatenate([[0.0], t])
            f = np.concatenate([[0.0], f])
        if f[-1] != 0.0:
            t = np.concatenate([t, [t[-1] + 1e-3]])
            f = np.concatenate([f, [0.0]])
        if self.propellant_mass is not None and not (0 < self.propellant_mass <= self.total_mass):
            raise ValueError("propellant mass must be in (0, total_mass]")
        object.__setattr__(self, "time", t)
        object.__setattr__(self, "thrust", f)
        cum = np.concatenate([[0.0], np.cumsum(0.5 * (f[1:] + f[:-1]) * np.diff(t))])
        object.__setattr__(self, "_cum", cum)

    # ---- performance ---------------------------------------------------
    @property
    def total_impulse(self) -> float:
        return float(self._cum[-1])

    @property
    def burn_time(self) -> float:
        return float(self.time[-1])

    @property
    def peak_thrust(self) -> float:
        return float(self.thrust.max())

    @property
    def average_thrust(self) -> float:
        return self.total_impulse / self.burn_time

    @property
    def classification(self) -> str:
        return f"{impulse_class(self.total_impulse)}{self.average_thrust:.0f}"

    @property
    def spent_mass(self) -> float:
        return self.total_mass - (self.propellant_mass or 0.0)

    @property
    def data_hash(self) -> str:
        return stable_hash({"t": self.time, "f": self.thrust, "m": self.total_mass,
                            "mp": self.propellant_mass, "meta": self.metadata})

    def thrust_at(self, t: float) -> float:
        if t <= 0.0 or t >= self.time[-1]:
            return 0.0
        return float(np.interp(t, self.time, self.thrust))

    def impulse_at(self, t: float) -> float:
        return float(np.interp(t, self.time, self._cum, left=0.0, right=self._cum[-1]))

    def propellant_fraction_remaining(self, t: float) -> float:
        """Assumes propellant is consumed proportionally to delivered impulse
        (the conventional RASP/OpenRocket assumption; an ESTIMATE)."""
        return 1.0 - self.impulse_at(t) / self.total_impulse

    def mass_at(self, t: float) -> float:
        mp = self.propellant_mass or 0.0
        return self.spent_mass + mp * self.propellant_fraction_remaining(t)

    def scaled(self, impulse_scale: float = 1.0, time_scale: float = 1.0) -> "MotorPerformance":
        """Perturbed copy for Monte Carlo. Burn-time scaling keeps impulse fixed
        unless ``impulse_scale`` also changes. Data quality is downgraded to
        HYPOTHETICAL so a perturbed curve can never be mistaken for the source."""
        meta = replace(self.metadata, data_quality=DataQuality.HYPOTHETICAL,
                       notes=f"perturbed from {self.metadata.data_quality.value} "
                             f"(I x{impulse_scale:.4f}, t x{time_scale:.4f})")
        return MotorPerformance(
            time=self.time * time_scale,
            thrust=self.thrust * impulse_scale / time_scale,
            total_mass=self.total_mass, propellant_mass=self.propellant_mass,
            metadata=meta, thrust_uncertainty_rel=self.thrust_uncertainty_rel)

    def summary(self) -> dict[str, object]:
        return {
            "designation": self.metadata.designation,
            "manufacturer": self.metadata.manufacturer,
            "classification": self.classification,
            "total_impulse_Ns": round(self.total_impulse, 2),
            "burn_time_s": round(self.burn_time, 3),
            "average_thrust_N": round(self.average_thrust, 1),
            "peak_thrust_N": round(self.peak_thrust, 1),
            "total_mass_kg": self.total_mass,
            "propellant_mass_kg": self.propellant_mass,
            "source": self.metadata.source,
            "source_date": self.metadata.source_date,
            "data_quality": self.metadata.data_quality.value,
        }


def synthetic_motor(designation: str = "SYNTH-H200", total_impulse: float = 300.0,
                    burn_time: float = 1.5, total_mass: float = 0.35,
                    propellant_mass: float = 0.18, n: int = 40) -> MotorPerformance:
    """Smooth, clearly-labelled HYPOTHETICAL thrust curve for examples and tests.
    It is not a real motor; never use it for flight planning."""
    t = np.linspace(0.0, burn_time, n)
    shape = np.sin(np.pi * t / burn_time) ** 0.6 * (1.15 - 0.3 * t / burn_time)
    shape[0] = shape[-1] = 0.0
    thrust = shape * total_impulse / _trapz(shape, t)
    meta = MotorMetadata(manufacturer="AERODYNE (synthetic)", designation=designation,
                         source="aerodyne.propulsion.motor.synthetic_motor",
                         source_date="2026-01-01", data_quality=DataQuality.HYPOTHETICAL,
                         diameter_mm=38.0, length_mm=250.0,
                         notes="Synthetic example curve. NOT a real motor.")
    return MotorPerformance(time=t, thrust=thrust, total_mass=total_mass,
                            propellant_mass=propellant_mass, metadata=meta)


def as_array(values: Sequence[float]) -> np.ndarray:
    return np.asarray(values, dtype=float)
