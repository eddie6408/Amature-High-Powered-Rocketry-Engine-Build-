"""Navigation / state estimation.

* :class:`AltitudeKalmanFilter` - 2-state (altitude, vertical velocity) filter
  using vertical acceleration as the control input and barometric altitude as
  the measurement. Identical maths to ``firmware/src/kalman.c``.
* :class:`AttitudePropagator` - gyro strap-down quaternion integration.

Raw sensor data is preserved separately by the logger; these produce DERIVED
estimates only.
"""

from __future__ import annotations

import math

import numpy as np

from aerodyne.dynamics import quaternion as quat


class AltitudeKalmanFilter:
    def __init__(self, accel_sigma: float = 2.0, baro_sigma: float = 1.5,
                 initial_altitude: float = 0.0) -> None:
        self.x = np.array([initial_altitude, 0.0])
        self.P = np.diag([10.0, 1.0])
        self.accel_sigma = accel_sigma
        self.baro_sigma = baro_sigma

    def predict(self, a_vertical: float, dt: float, accel_sigma: float | None = None) -> None:
        s = self.accel_sigma if accel_sigma is None else accel_sigma
        F = np.array([[1.0, dt], [0.0, 1.0]])
        B = np.array([0.5 * dt * dt, dt])
        G = B.reshape(2, 1)
        self.x = F @ self.x + B * a_vertical
        self.P = F @ self.P @ F.T + (G @ G.T) * s * s

    def update(self, baro_altitude: float, baro_sigma: float | None = None) -> float:
        """Returns the normalized innovation (for outlier gating by the caller)."""
        r = (self.baro_sigma if baro_sigma is None else baro_sigma) ** 2
        y = baro_altitude - self.x[0]
        s = self.P[0, 0] + r
        k = self.P[:, 0] / s
        self.x = self.x + k * y
        self.P = self.P - np.outer(k, self.P[0, :])
        return y / math.sqrt(s)

    def innovation(self, baro_altitude: float, baro_sigma: float | None = None) -> float:
        r = (self.baro_sigma if baro_sigma is None else baro_sigma) ** 2
        return (baro_altitude - self.x[0]) / math.sqrt(self.P[0, 0] + r)

    def set_state(self, altitude: float, velocity: float) -> None:
        self.x = np.array([altitude, velocity], dtype=float)

    @property
    def altitude(self) -> float:
        return float(self.x[0])

    @property
    def velocity(self) -> float:
        return float(self.x[1])


class AttitudePropagator:
    def __init__(self, q0: np.ndarray | None = None) -> None:
        self.q = np.array([1.0, 0.0, 0.0, 0.0]) if q0 is None else quat.normalize(q0)

    def propagate(self, gyro: np.ndarray, dt: float) -> None:
        w = np.asarray(gyro, dtype=float)
        ang = float(np.linalg.norm(w)) * dt
        if ang < 1e-12:
            return
        dq = quat.from_axis_angle(w, ang)
        self.q = quat.normalize(quat.multiply(self.q, dq))

    def tilt(self) -> float:
        """Angle between body x and world up (rad)."""
        x_world = quat.to_matrix(self.q)[:, 0]
        return math.acos(max(-1.0, min(1.0, x_world[2])))
