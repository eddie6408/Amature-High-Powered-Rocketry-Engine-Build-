"""Virtual sensors: turn a SimulationResult into sampled, noisy sensor readings
(with a pad-wait segment before liftoff and a post-landing segment)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from aerodyne.avionics.sensors import SensorReading, Status
from aerodyne.dynamics import quaternion as quat
from aerodyne.dynamics.simulator import SimulationResult
from aerodyne.environment.atmosphere import G0, StandardAtmosphere


@dataclass(frozen=True)
class SensorNoise:
    accel_sigma: float = 0.3        # m/s^2
    accel_bias: float = 0.1
    gyro_sigma: float = 0.005       # rad/s
    baro_sigma: float = 0.6         # m
    gnss_sigma: float = 3.0         # m
    accel_range: float = 392.0      # m/s^2 (+-40 g)


RATES = {"imu_accel": 100.0, "imu_gyro": 100.0, "baro": 50.0, "gnss": 10.0, "battery": 2.0,
         "temperature": 1.0}


class VirtualSensors:
    def __init__(self, sim: SimulationResult, noise: SensorNoise | None = None, seed: int = 0,
                 pad_time: float = 8.0, post_landing_time: float = 12.0) -> None:
        self.sim = sim
        self.noise = noise or SensorNoise()
        self.rng = np.random.default_rng(seed)
        self.pad_time = pad_time
        self.t_end = float(sim.t[-1]) + post_landing_time
        self._seq = {k: 0 for k in RATES}
        self._next = {k: -pad_time for k in RATES}
        self.baro_model = StandardAtmosphere()
        self._q0 = sim.attitude[0]
        self._land_q = sim.attitude[-1]
        self.battery_v = 8.2

    def truth(self, t: float) -> dict[str, np.ndarray | float]:
        s = self.sim
        if t < 0:
            R = quat.to_matrix(self._q0)
            return {"pos": np.zeros(3), "sf": R.T @ np.array([0, 0, G0]), "w": np.zeros(3),
                    "p": float(s.pressure[0]), "T": float(s.temperature[0])}
        if t > s.t[-1]:
            # landed: at rest, lying on its side
            R = quat.to_matrix(quat.from_two_vectors(np.array([1.0, 0, 0]), np.array([1.0, 0, 0.05])))
            return {"pos": s.position[-1] * np.array([1, 1, 0]), "sf": R.T @ np.array([0, 0, G0]),
                    "w": np.zeros(3), "p": float(s.pressure[0]), "T": float(s.temperature[0])}
        i = min(int(np.searchsorted(s.t, t)), len(s.t) - 1)
        j = max(i - 1, 0)
        a = 0.0 if s.t[i] == s.t[j] else (t - s.t[j]) / (s.t[i] - s.t[j])
        lerp = lambda arr: arr[j] + a * (arr[i] - arr[j])
        return {"pos": lerp(s.position), "sf": lerp(s.specific_force_body),
                "w": lerp(s.angular_velocity), "p": float(lerp(s.pressure)),
                "T": float(lerp(s.temperature))}

    def sample(self, t: float) -> dict[str, SensorReading]:
        """Readings with a new sample at time t (liftoff = 0)."""
        out: dict[str, SensorReading] = {}
        due = [k for k in RATES if t + 1e-9 >= self._next[k]]
        if not due:
            return out
        tr = self.truth(t)
        n = self.noise
        for k in due:
            self._next[k] += 1.0 / RATES[k]
            self._seq[k] += 1
            if k == "imu_accel":
                v = tr["sf"] + n.accel_bias + self.rng.normal(0, n.accel_sigma, 3)
                val = tuple(float(x) for x in np.clip(v, -n.accel_range, n.accel_range))
            elif k == "imu_gyro":
                val = tuple(float(x) for x in tr["w"] + self.rng.normal(0, n.gyro_sigma, 3))
            elif k == "baro":
                val = self.baro_model.altitude_from_pressure(tr["p"]) + float(
                    self.rng.normal(0, n.baro_sigma))
            elif k == "gnss":
                e, nn, u = tr["pos"] + self.rng.normal(0, n.gnss_sigma, 3)
                lat, lon = self.sim.site.to_geodetic(float(e), float(nn))
                val = (lat, lon, float(u + self.sim.site.altitude_msl))
            elif k == "battery":
                self.battery_v -= 0.0005
                val = self.battery_v + float(self.rng.normal(0, 0.01))
            else:
                val = tr["T"] - 273.15 + 15.0 + float(self.rng.normal(0, 0.2))
            out[k] = SensorReading(sensor_id=k, kind=k, value=val, timestamp=t,
                                   sequence=self._seq[k], status=Status.OK,
                                   quality=0.8 if k == "gnss" else 1.0)
        return out


def gnss_quality_to_sats(q: float) -> int:
    return int(math.floor(q * 12))
