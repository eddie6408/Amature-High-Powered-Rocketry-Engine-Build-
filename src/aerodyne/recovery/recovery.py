"""Recovery configuration and descent analysis (ESTIMATED / SIMULATED)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from aerodyne.core.provenance import DataKind
from aerodyne.environment.atmosphere import G0, AtmosphereModel
from aerodyne.environment.wind import WindModel


@dataclass(frozen=True)
class RecoveryDevice:
    name: str
    cd: float                    # drag coefficient referenced to nominal area
    diameter: float              # m, nominal (canopy) diameter
    deploy_event: str = "apogee"  # "apogee" | "altitude"
    deploy_altitude_agl: float | None = None   # m, when deploy_event == "altitude"
    delay: float = 0.0           # s after the event
    inflation_time: float = 0.8  # s to reach full drag area
    opening_load_factor: float = 1.5   # Cx for opening-shock estimate (ESTIMATE)

    @property
    def area(self) -> float:
        return math.pi * self.diameter ** 2 / 4

    @property
    def cd_area(self) -> float:
        return self.cd * self.area


@dataclass(frozen=True)
class RecoveryConfig:
    devices: tuple[RecoveryDevice, ...]
    body_cd_area: float = 0.01      # m^2, tumbling airframe drag area after separation

    def deployed_cd_area(self, deploy_times: dict[str, float], t: float) -> float:
        total = self.body_cd_area
        for d in self.devices:
            t0 = deploy_times.get(d.name)
            if t0 is None or t < t0:
                continue
            frac = min(1.0, (t - t0) / d.inflation_time) if d.inflation_time > 0 else 1.0
            total += d.cd_area * frac
        return total


def descent_rate(mass: float, cd_area: float, density: float) -> float:
    """Steady-state terminal descent rate, m/s."""
    return math.sqrt(2 * mass * G0 / (density * cd_area))


@dataclass
class RecoveryEstimate:
    descent_rates: dict[str, float]          # m/s under each stage at its reference altitude
    timeline: list[tuple[float, str]]        # (t after apogee, event)
    descent_time: float                      # s from apogee to ground
    drift: np.ndarray                        # m (east, north) from apogee ground point
    drift_distance: float
    opening_loads: dict[str, float]          # N (ESTIMATE, Cx q CdA)
    kind: DataKind = DataKind.SIMULATED
    notes: list[str] = field(default_factory=list)


def analyze_recovery(mass: float, config: RecoveryConfig, apogee_agl: float,
                     site_altitude: float, atmosphere: AtmosphereModel, wind: WindModel,
                     apogee_speed: float = 0.0, dt: float = 0.05) -> RecoveryEstimate:
    """Integrate a point-mass descent from apogee with the configured devices,
    drifting with the wind. Independent of any propulsion input."""
    deploy_times: dict[str, float] = {}
    timeline: list[tuple[float, str]] = [(0.0, "apogee")]
    pos = np.array([0.0, 0.0, apogee_agl])
    vel = np.array([apogee_speed, 0.0, 0.0])
    t = 0.0
    opening: dict[str, float] = {}
    rates: dict[str, float] = {}
    while pos[2] > 0 and t < 3600:
        for d in config.devices:
            if d.name in deploy_times:
                continue
            fire = (d.deploy_event == "apogee" and t >= d.delay) or (
                d.deploy_event == "altitude" and d.deploy_altitude_agl is not None
                and pos[2] <= d.deploy_altitude_agl)
            if fire:
                deploy_times[d.name] = t + (d.delay if d.deploy_event == "altitude" else 0.0)
                atm = atmosphere.at(site_altitude + pos[2])
                w = wind.at(pos[2], t)
                v_rel = np.linalg.norm(vel - w)
                opening[d.name] = d.opening_load_factor * 0.5 * atm.density * v_rel ** 2 * d.cd_area
                timeline.append((deploy_times[d.name], f"{d.name} deploy"))
                full_cda = config.body_cd_area + sum(
                    dd.cd_area for dd in config.devices if dd.name in deploy_times)
                rates[d.name] = descent_rate(mass, full_cda, atm.density)
        atm = atmosphere.at(site_altitude + pos[2])
        w = wind.at(pos[2], t)
        cda = config.deployed_cd_area(deploy_times, t)
        v_rel = vel - w
        drag = -0.5 * atm.density * np.linalg.norm(v_rel) * v_rel * cda
        acc = drag / mass + np.array([0.0, 0.0, -G0])
        vel = vel + acc * dt
        pos = pos + vel * dt
        t += dt
    timeline.append((t, "landing"))
    notes = ["Opening loads use Cx * q * CdA with an assumed Cx; verify against "
             "manufacturer data and test."]
    return RecoveryEstimate(descent_rates=rates, timeline=timeline, descent_time=t,
                            drift=pos[:2].copy(), drift_distance=float(np.linalg.norm(pos[:2])),
                            opening_loads=opening, notes=notes)
