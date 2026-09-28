"""Generic sensor framework and sensor validation.

Every reading carries value, timestamp, health, quality and status. A sensor
failing validation is marked DEGRADED (then FAILED if it persists) - its data
is never silently used.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Health(IntEnum):
    OK = 0
    DEGRADED = 1
    FAILED = 2
    UNKNOWN = 3


class Status(IntEnum):
    OK = 0
    COMM_ERROR = 1
    NOT_READY = 2
    NO_FIX = 3


@dataclass(frozen=True)
class SensorReading:
    sensor_id: str
    kind: str              # imu_accel | imu_gyro | baro | gnss | temperature | battery
    value: Any             # float or tuple
    timestamp: float       # s, sensor/system monotonic clock
    sequence: int
    status: Status = Status.OK
    quality: float = 1.0   # 0..1, e.g. GNSS from HDOP / sats
    health: Health = Health.UNKNOWN   # filled in by the validator


@dataclass
class ValidationLimits:
    min_value: float = -math.inf
    max_value: float = math.inf
    max_rate: float = math.inf          # |dv/dt| per second
    stale_timeout: float = 0.5          # s without a fresh sample
    stuck_count: int = 0                # identical values in a row -> stale (0 disables)
    outlier_window: int = 0             # samples for median/MAD outlier test (0 disables)
    outlier_k: float = 8.0
    outlier_floor: float = 0.0          # minimum deviation (units of value) to call an outlier
    degrade_after: int = 1              # consecutive failures -> DEGRADED
    fail_after: int = 25                # consecutive failures -> FAILED
    recover_after: int = 10             # consecutive good samples -> OK
    component: int | None = None        # validate this element of a vector value (else norm)


@dataclass
class ValidationResult:
    ok: bool
    health: Health
    failures: list[str] = field(default_factory=list)


def _scalar(v: Any, component: int | None = None) -> float:
    if isinstance(v, (tuple, list)):
        if component is not None:
            return float(v[component])
        return float(math.sqrt(sum(float(x) ** 2 for x in v)))
    return float(v)


class SensorValidator:
    """Stateful validator for one sensor channel: range, rate of change,
    timestamp validity, communication status, missing, stale and outliers."""

    def __init__(self, limits: ValidationLimits) -> None:
        self.lim = limits
        self.health = Health.UNKNOWN
        self._last_t: float | None = None
        self._last_v: float | None = None
        self._last_good_t: float | None = None
        self._same = 0
        self._bad = 0
        self._good = 0
        self._hist: deque[float] = deque(maxlen=max(limits.outlier_window, 1))
        self.fail_counts: dict[str, int] = {}

    def check(self, r: SensorReading | None, now: float) -> ValidationResult:
        fails: list[str] = []
        if r is None:
            fails.append("missing")
        else:
            if r.status != Status.OK:
                fails.append(f"status:{r.status.name}")
            try:
                v = _scalar(r.value, self.lim.component)
            except (TypeError, ValueError):
                v = math.nan
            if not math.isfinite(v):
                fails.append("missing")
            else:
                if not (self.lim.min_value <= v <= self.lim.max_value):
                    fails.append("range")
                if self._last_t is not None:
                    if r.timestamp <= self._last_t:
                        fails.append("timestamp")
                    elif self._last_v is not None:
                        rate = abs(v - self._last_v) / (r.timestamp - self._last_t)
                        if rate > self.lim.max_rate:
                            fails.append("rate")
                if r.timestamp > now + 0.05:
                    fails.append("timestamp")
                if self.lim.stuck_count and self._last_v is not None and v == self._last_v:
                    self._same += 1
                    if self._same >= self.lim.stuck_count:
                        fails.append("stale")
                else:
                    self._same = 0
                if self.lim.outlier_window and len(self._hist) >= self.lim.outlier_window:
                    arr = sorted(self._hist)
                    med = arr[len(arr) // 2]
                    mad = sorted(abs(x - med) for x in arr)[len(arr) // 2] or 1e-6
                    if abs(v - med) > max(self.lim.outlier_k * 1.4826 * mad, self.lim.outlier_floor):
                        fails.append("outlier")
            if r is not None and "timestamp" not in fails:
                self._last_t = r.timestamp
            if math.isfinite(v) and not {"range", "rate"} & set(fails):
                # the median/MAD test is robust to the odd spike in its history, and
                # admitting every in-range sample lets it follow genuine changes
                self._hist.append(v)
                if "outlier" not in fails:
                    self._last_v = v
        if (self._last_good_t is not None and now - self._last_good_t > self.lim.stale_timeout
                and "missing" in fails):
            fails.append("stale")

        for f in fails:
            self.fail_counts[f] = self.fail_counts.get(f, 0) + 1
        if fails:
            self._bad += 1
            self._good = 0
            if self._bad >= self.lim.fail_after:
                self.health = Health.FAILED
            elif self._bad >= self.lim.degrade_after and self.health != Health.FAILED:
                self.health = Health.DEGRADED
        else:
            self._good += 1
            self._bad = 0
            self._last_good_t = now
            if self.health in (Health.UNKNOWN,) or self._good >= self.lim.recover_after:
                self.health = Health.OK
        return ValidationResult(ok=not fails, health=self.health, failures=fails)


DEFAULT_LIMITS: dict[str, ValidationLimits] = {
    # accelerometer specific force along the body axis (m/s^2); +-400 m/s^2 ~ 40 g part
    "imu_accel": ValidationLimits(-400.0, 400.0, max_rate=20000.0, stale_timeout=0.05,
                                  stuck_count=50, outlier_window=0, fail_after=20),
    "imu_gyro": ValidationLimits(-35.0, 35.0, max_rate=5000.0, stale_timeout=0.05,
                                 stuck_count=0, fail_after=20),
    # barometric altitude (m MSL). Rate limit ~ 3x max plausible vertical speed.
    "baro": ValidationLimits(-500.0, 30000.0, max_rate=1500.0, stale_timeout=0.2,
                             stuck_count=100, outlier_window=15, outlier_floor=30.0,
                             fail_after=20),
    "gnss": ValidationLimits(-500.0, 30000.0, max_rate=2000.0, stale_timeout=2.0,
                             stuck_count=0, fail_after=10, component=2),   # altitude MSL
    "battery": ValidationLimits(0.0, 20.0, max_rate=50.0, stale_timeout=2.0, fail_after=5),
    "temperature": ValidationLimits(-60.0, 125.0, max_rate=50.0, stale_timeout=5.0, fail_after=5),
}
