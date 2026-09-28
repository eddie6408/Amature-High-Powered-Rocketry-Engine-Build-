"""Automated preflight verification.

Any failed *critical* check yields NOT READY - the system never silently
proceeds. Non-critical failures yield READY (WITH WARNINGS) and are listed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from aerodyne.avionics.firmware import FirmwareIdentity
from aerodyne.avionics.sensors import Health


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    critical: bool
    detail: str = ""


@dataclass
class PreflightReport:
    results: list[CheckResult]

    @property
    def ready(self) -> bool:
        return all(r.passed for r in self.results if r.critical)

    @property
    def status(self) -> str:
        if not self.ready:
            return "NOT READY"
        if any(not r.passed for r in self.results):
            return "READY (WITH WARNINGS)"
        return "READY"

    def render(self) -> str:
        lines = []
        for r in self.results:
            mark = "✓" if r.passed else ("✗" if r.critical else "!")
            lines.append(f"[{mark}] {r.name}" + (f" - {r.detail}" if r.detail and not r.passed else ""))
        lines += ["", "SYSTEM STATUS:", self.status]
        return "\n".join(lines)


@dataclass
class FlightComputerStatus:
    """Snapshot reported by the flight computer (or HIL/SIL harness)."""

    detected: bool
    identity: FirmwareIdentity | None
    storage_free_bytes: int
    sensor_health: dict[str, Health]
    battery_v: float
    clock_valid: bool
    telemetry_link: bool
    gnss_sats: int = 0


@dataclass
class PreflightRequirements:
    expected_identity: FirmwareIdentity
    min_storage_bytes: int = 16 * 1024 * 1024
    min_battery_v: float = 7.4
    min_gnss_sats: int = 6
    critical_sensors: tuple[str, ...] = ("imu_accel", "imu_gyro", "baro")
    gnss_critical: bool = False
    telemetry_critical: bool = False


def run_preflight(fc: FlightComputerStatus, req: PreflightRequirements,
                  vehicle_config_loaded: bool, simulation_config_loaded: bool,
                  extra: list[Callable[[], CheckResult]] | None = None) -> PreflightReport:
    r: list[CheckResult] = []
    r.append(CheckResult("Flight computer detected", fc.detected, True))
    if fc.identity is None:
        r.append(CheckResult("Firmware verified", False, True, "no identity reported"))
        r.append(CheckResult("Configuration verified", False, True, "no identity reported"))
    else:
        mm = fc.identity.mismatches(req.expected_identity)
        fw = [m for m in mm if m != "config_hash"]
        r.append(CheckResult("Firmware verified", not fw, True, f"mismatch: {', '.join(fw)}"))
        r.append(CheckResult("Configuration verified", "config_hash" not in mm, True,
                             "config hash mismatch"))
    r.append(CheckResult("Storage available", fc.storage_free_bytes >= req.min_storage_bytes, True,
                         f"{fc.storage_free_bytes} B free < {req.min_storage_bytes} B"))
    for name, label in (("imu_accel", "IMU accelerometer healthy"), ("imu_gyro", "IMU gyro healthy"),
                        ("baro", "Barometer healthy")):
        h = fc.sensor_health.get(name, Health.UNKNOWN)
        r.append(CheckResult(label, h == Health.OK, name in req.critical_sensors, h.name))
    gh = fc.sensor_health.get("gnss", Health.UNKNOWN)
    r.append(CheckResult("GNSS healthy", gh == Health.OK and fc.gnss_sats >= req.min_gnss_sats,
                         req.gnss_critical, f"{gh.name}, {fc.gnss_sats} sats"))
    r.append(CheckResult("Battery healthy", fc.battery_v >= req.min_battery_v, True,
                         f"{fc.battery_v:.2f} V < {req.min_battery_v:.2f} V"))
    r.append(CheckResult("Clock valid", fc.clock_valid, True))
    r.append(CheckResult("Telemetry available", fc.telemetry_link, req.telemetry_critical))
    r.append(CheckResult("Vehicle configuration loaded", vehicle_config_loaded, True))
    r.append(CheckResult("Simulation configuration loaded", simulation_config_loaded, True))
    for fn in extra or []:
        r.append(fn())
    return PreflightReport(r)
