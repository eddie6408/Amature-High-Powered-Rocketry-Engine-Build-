"""Wind models, independent of the vehicle.

Convention: returned vectors are the air velocity in the local ENU frame
(east, north, up) m/s. Directions are meteorological: the direction the wind
blows FROM, degrees clockwise from true north.
"""

from __future__ import annotations

import math
from typing import Protocol, Sequence

import numpy as np


def wind_vector(speed: float, from_direction_deg: float) -> np.ndarray:
    to = math.radians(from_direction_deg + 180.0)
    return np.array([speed * math.sin(to), speed * math.cos(to), 0.0])


class WindModel(Protocol):
    def at(self, altitude_agl: float, t: float) -> np.ndarray: ...


class ConstantWind:
    def __init__(self, speed: float = 0.0, from_direction_deg: float = 0.0) -> None:
        self.speed = speed
        self.direction = from_direction_deg
        self._v = wind_vector(speed, from_direction_deg)

    def at(self, altitude_agl: float, t: float) -> np.ndarray:
        return self._v.copy()


class PowerLawWind:
    """Boundary-layer power law: v(h) = v_ref (h / h_ref)^alpha."""

    def __init__(self, reference_speed: float, from_direction_deg: float,
                 reference_height: float = 10.0, exponent: float = 1.0 / 7.0) -> None:
        self.reference_speed = reference_speed
        self.direction = from_direction_deg
        self.reference_height = reference_height
        self.exponent = exponent

    def at(self, altitude_agl: float, t: float) -> np.ndarray:
        h = max(altitude_agl, 0.5)
        return wind_vector(self.reference_speed * (h / self.reference_height) ** self.exponent,
                           self.direction)


class LayeredWind:
    """Altitude-dependent (e.g. measured / forecast) wind profile. Interpolates
    the east and north components separately to avoid direction wrap problems."""

    def __init__(self, altitude_agl: Sequence[float], speed: Sequence[float],
                 from_direction_deg: Sequence[float]) -> None:
        order = np.argsort(np.asarray(altitude_agl, dtype=float))
        self.alt = np.asarray(altitude_agl, dtype=float)[order]
        vecs = np.array([wind_vector(s, d) for s, d in zip(np.asarray(speed)[order],
                                                            np.asarray(from_direction_deg)[order])])
        self.east = vecs[:, 0]
        self.north = vecs[:, 1]

    def at(self, altitude_agl: float, t: float) -> np.ndarray:
        return np.array([np.interp(altitude_agl, self.alt, self.east),
                         np.interp(altitude_agl, self.alt, self.north), 0.0])


class GustWind:
    """Probabilistic wind: a base model plus first-order Gauss-Markov gusts.
    Deterministic for a given seed so Monte Carlo runs are reproducible."""

    def __init__(self, base: WindModel, gust_sigma: float = 1.0, correlation_time: float = 2.0,
                 seed: int | None = None, dt: float = 0.05) -> None:
        self.base = base
        self.sigma = gust_sigma
        self.tau = correlation_time
        self.dt = dt
        self._rng = np.random.default_rng(seed)
        self._t = 0.0
        self._g = np.zeros(3)

    def at(self, altitude_agl: float, t: float) -> np.ndarray:
        # advance the gust process up to time t (monotonic calls expected)
        while self._t + self.dt <= t:
            a = math.exp(-self.dt / self.tau)
            noise = self._rng.normal(0.0, self.sigma * math.sqrt(1 - a * a), 3)
            noise[2] *= 0.3  # vertical gusts are weaker
            self._g = a * self._g + noise
            self._t += self.dt
        return self.base.at(altitude_agl, t) + self._g
