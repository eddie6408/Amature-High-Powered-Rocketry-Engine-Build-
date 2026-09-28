"""Automated fault scenarios."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FaultKind(str, Enum):
    IMU_FAILURE = "IMU_FAILURE"
    BAROMETER_FAILURE = "BAROMETER_FAILURE"
    GNSS_LOSS = "GNSS_LOSS"
    TELEMETRY_LOSS = "TELEMETRY_LOSS"
    SENSOR_SPIKE = "SENSOR_SPIKE"
    STALE_DATA = "STALE_DATA"
    CORRUPTED_PACKET = "CORRUPTED_PACKET"
    LOW_BATTERY = "LOW_BATTERY"
    CLOCK_ERROR = "CLOCK_ERROR"
    STORAGE_FAILURE = "STORAGE_FAILURE"
    PROCESSOR_RESET = "PROCESSOR_RESET"


@dataclass(frozen=True)
class FaultInjection:
    kind: FaultKind
    start: float                     # s, relative to liftoff (negative = on the pad)
    end: float | None = None         # None = until the end of the run
    sensor: str = ""                 # for SENSOR_SPIKE / STALE_DATA
    magnitude: float = 0.0           # spike size, clock jump (s), battery voltage...
    params: dict = field(default_factory=dict)

    def active(self, t: float) -> bool:
        return t >= self.start and (self.end is None or t < self.end)


# Canonical scenario set exercised by the test-suite and `aerodyne sil --all-faults`.
STANDARD_SCENARIOS: dict[str, list[FaultInjection]] = {
    "nominal": [],
    "imu_failure_coast": [FaultInjection(FaultKind.IMU_FAILURE, start=3.0)],
    "baro_failure_boost": [FaultInjection(FaultKind.BAROMETER_FAILURE, start=0.5)],
    "gnss_loss": [FaultInjection(FaultKind.GNSS_LOSS, start=-1.0)],
    "telemetry_loss": [FaultInjection(FaultKind.TELEMETRY_LOSS, start=2.0, end=6.0)],
    "baro_spike_pad": [FaultInjection(FaultKind.SENSOR_SPIKE, start=-2.0, sensor="baro",
                                      magnitude=400.0)],
    "accel_spike_pad": [FaultInjection(FaultKind.SENSOR_SPIKE, start=-2.0, sensor="imu_accel",
                                       magnitude=300.0)],
    "baro_spike_coast": [FaultInjection(FaultKind.SENSOR_SPIKE, start=5.0, sensor="baro",
                                        magnitude=-300.0)],
    "stale_baro": [FaultInjection(FaultKind.STALE_DATA, start=4.0, end=9.0, sensor="baro")],
    "corrupted_packets": [FaultInjection(FaultKind.CORRUPTED_PACKET, start=1.0, end=10.0,
                                         params={"probability": 0.3})],
    "low_battery": [FaultInjection(FaultKind.LOW_BATTERY, start=5.0, magnitude=6.4)],
    "clock_error": [FaultInjection(FaultKind.CLOCK_ERROR, start=4.0, magnitude=-0.5)],
    "storage_failure": [FaultInjection(FaultKind.STORAGE_FAILURE, start=2.0)],
    "processor_reset_coast": [FaultInjection(FaultKind.PROCESSOR_RESET, start=6.0)],
}
