"""Modular atmosphere models.

* :class:`StandardAtmosphere` - U.S. Standard Atmosphere 1976, 0-86 km geometric,
  optionally offset in temperature (hot/cold day) and ground pressure.
* :class:`ProfileAtmosphere` - measured sounding (radiosonde / weather model)
  interpolated by altitude.

Altitudes are geometric metres above mean sea level (MSL).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np

from aerodyne.core.provenance import DataKind

R_AIR = 287.05287          # J/(kg K), dry air
GAMMA = 1.4
G0 = 9.80665               # m/s^2
EARTH_RADIUS = 6356766.0   # m, US1976 effective radius for geopotential conversion
R_VAPOR = 461.495          # J/(kg K)


@dataclass(frozen=True)
class AtmosphereState:
    altitude: float          # m MSL (geometric)
    temperature: float       # K
    pressure: float          # Pa
    density: float           # kg/m^3
    speed_of_sound: float    # m/s
    dynamic_viscosity: float  # Pa s
    kind: DataKind

    @property
    def kinematic_viscosity(self) -> float:
        return self.dynamic_viscosity / self.density


class AtmosphereModel(Protocol):
    def at(self, altitude: float) -> AtmosphereState: ...


def sutherland_viscosity(temperature: float) -> float:
    return 1.458e-6 * temperature ** 1.5 / (temperature + 110.4)


def _moist_density(pressure: float, temperature: float, relative_humidity: float) -> float:
    """Density of moist air (Tetens saturation pressure)."""
    if relative_humidity <= 0.0:
        return pressure / (R_AIR * temperature)
    t_c = temperature - 273.15
    p_sat = 610.78 * 10 ** (7.5 * t_c / (t_c + 237.3))
    p_v = min(max(relative_humidity, 0.0), 1.0) * p_sat
    p_d = pressure - p_v
    return p_d / (R_AIR * temperature) + p_v / (R_VAPOR * temperature)


# US1976 layers: base geopotential altitude (m), base temperature (K), lapse rate (K/m)
_LAYERS = [
    (0.0, 288.15, -0.0065),
    (11000.0, 216.65, 0.0),
    (20000.0, 216.65, 0.0010),
    (32000.0, 228.65, 0.0028),
    (47000.0, 270.65, 0.0),
    (51000.0, 270.65, -0.0028),
    (71000.0, 214.65, -0.0020),
    (84852.0, 186.946, 0.0),
]


def _layer_pressures(p0: float) -> list[float]:
    pressures = [p0]
    for i in range(1, len(_LAYERS)):
        hb, tb, lb = _LAYERS[i - 1]
        h = _LAYERS[i][0]
        pb = pressures[-1]
        if lb == 0.0:
            pressures.append(pb * math.exp(-G0 * (h - hb) / (R_AIR * tb)))
        else:
            pressures.append(pb * ((tb + lb * (h - hb)) / tb) ** (-G0 / (R_AIR * lb)))
    return pressures


class StandardAtmosphere:
    """US Standard Atmosphere 1976 with optional temperature offset (K) and
    humidity. ``temperature_offset`` shifts the whole profile, which is the usual
    way to represent a hot/cold launch day."""

    def __init__(self, temperature_offset: float = 0.0, sea_level_pressure: float = 101325.0,
                 relative_humidity: float = 0.0) -> None:
        self.temperature_offset = temperature_offset
        self.sea_level_pressure = sea_level_pressure
        self.relative_humidity = relative_humidity
        self._pb = _layer_pressures(sea_level_pressure)

    @staticmethod
    def geopotential(z: float) -> float:
        return EARTH_RADIUS * z / (EARTH_RADIUS + z)

    def at(self, altitude: float) -> AtmosphereState:
        z = min(max(altitude, -5000.0), 86000.0)
        h = self.geopotential(z)
        idx = 0
        for i, layer in enumerate(_LAYERS):
            if h >= layer[0]:
                idx = i
        hb, tb, lb = _LAYERS[idx]
        pb = self._pb[idx]
        t_std = tb + lb * (h - hb)
        if lb == 0.0:
            p = pb * math.exp(-G0 * (h - hb) / (R_AIR * tb))
        else:
            p = pb * (t_std / tb) ** (-G0 / (R_AIR * lb))
        t = t_std + self.temperature_offset
        rho = _moist_density(p, t, self.relative_humidity)
        return AtmosphereState(
            altitude=altitude, temperature=t, pressure=p, density=rho,
            speed_of_sound=math.sqrt(GAMMA * R_AIR * t),
            dynamic_viscosity=sutherland_viscosity(t),
            kind=DataKind.ESTIMATED,
        )

    def altitude_from_pressure(self, pressure: float) -> float:
        """Invert the standard troposphere/lower-stratosphere relation, as a
        barometric altimeter does (it assumes standard temperatures, so on a
        non-standard day the result is pressure altitude, not true altitude).
        Returns geometric altitude."""
        p0 = self.sea_level_pressure
        if pressure >= self._pb[1]:
            h = (288.15 / 0.0065) * (1.0 - (pressure / p0) ** (R_AIR * 0.0065 / G0))
        else:
            h = 11000.0 + (R_AIR * 216.65 / G0) * math.log(self._pb[1] / pressure)
        return EARTH_RADIUS * h / (EARTH_RADIUS - h)


class ProfileAtmosphere:
    """Measured/forecast atmospheric profile. Above the top of the profile the
    standard atmosphere, matched at the top point, is used."""

    def __init__(self, altitude: Sequence[float], temperature: Sequence[float],
                 pressure: Sequence[float], relative_humidity: Sequence[float] | None = None,
                 kind: DataKind = DataKind.MEASURED) -> None:
        order = np.argsort(np.asarray(altitude, dtype=float))
        self.alt = np.asarray(altitude, dtype=float)[order]
        self.temp = np.asarray(temperature, dtype=float)[order]
        self.logp = np.log(np.asarray(pressure, dtype=float)[order])
        self.rh = (np.asarray(relative_humidity, dtype=float)[order]
                   if relative_humidity is not None else np.zeros_like(self.alt))
        if len(self.alt) < 2:
            raise ValueError("profile needs at least two levels")
        self.kind = kind
        top_std = StandardAtmosphere().at(self.alt[-1])
        self._above = StandardAtmosphere(
            temperature_offset=self.temp[-1] - top_std.temperature,
            sea_level_pressure=101325.0 * math.exp(self.logp[-1]) / top_std.pressure)

    def at(self, altitude: float) -> AtmosphereState:
        if altitude > self.alt[-1]:
            s = self._above.at(altitude)
            return AtmosphereState(**{**s.__dict__, "kind": DataKind.ESTIMATED})
        t = float(np.interp(altitude, self.alt, self.temp))
        p = float(math.exp(np.interp(altitude, self.alt, self.logp)))
        rh = float(np.interp(altitude, self.alt, self.rh))
        return AtmosphereState(
            altitude=altitude, temperature=t, pressure=p,
            density=_moist_density(p, t, rh), speed_of_sound=math.sqrt(GAMMA * R_AIR * t),
            dynamic_viscosity=sutherland_viscosity(t), kind=self.kind,
        )
