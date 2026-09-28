"""Ground-station state aggregation.

Displays only what telemetry supports: GNSS positions are shown with a
quality-dependent precision, and the landing estimate carries an uncertainty
radius instead of a falsely precise point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from aerodyne.avionics.sensors import Health
from aerodyne.avionics.state_machine import FlightState
from aerodyne.avionics.telemetry import TelemetryPacket, TelemetryReceiver, unpack_sensor_status

EARTH_R = 6371000.0


@dataclass
class VehicleStatus:
    state: str = "NO DATA"
    altitude_m: float | None = None
    velocity_mps: float | None = None
    acceleration_mps2: float | None = None
    battery_v: float | None = None
    temperature_c: float | None = None
    attitude: tuple[float, float, float, float] | None = None
    gnss_fix: int = 0
    gnss_sats: int = 0
    sensor_health: dict[str, str] = field(default_factory=dict)
    last_packet_time: float | None = None
    max_altitude_m: float = 0.0


def gnss_quality(fix: int, sats: int) -> tuple[str, float]:
    """(label, 1-sigma horizontal uncertainty in m) - conservative defaults."""
    if fix < 2:
        return "NO FIX", math.inf
    if fix == 2 or sats < 6:
        return "POOR", 25.0
    if sats < 9:
        return "FAIR", 8.0
    return "GOOD", 4.0


class GroundStation:
    def __init__(self, vehicle_id: int | None = None, link_timeout: float = 2.0) -> None:
        self.rx = TelemetryReceiver(vehicle_id=vehicle_id, link_timeout=link_timeout)
        self.status = VehicleStatus()
        self.launch_position: tuple[float, float] | None = None
        self.track: list[tuple[float, float, float, float]] = []    # (t, lat, lon, alt)
        self.history: list[TelemetryPacket] = []
        self.now = 0.0

    def feed(self, data: bytes, now: float) -> int:
        self.now = now
        n = 0
        for pkt, in_order in self.rx.feed(data, now):
            self.history.append(pkt)
            n += 1
            if in_order:
                self._apply(pkt, now)
        return n

    def _apply(self, p: TelemetryPacket, now: float) -> None:
        s = self.status
        s.state = FlightState(p.system_status).name if p.system_status <= 6 else f"?{p.system_status}"
        s.altitude_m, s.velocity_mps, s.acceleration_mps2 = p.altitude, p.velocity, p.acceleration
        s.max_altitude_m = max(s.max_altitude_m, p.altitude)
        s.battery_v = p.battery_mv / 1000.0
        s.temperature_c = p.temperature_c
        s.attitude = p.attitude
        s.gnss_fix, s.gnss_sats = p.gnss_fix, p.gnss_sats
        s.sensor_health = {k: Health(v).name for k, v in unpack_sensor_status(p.sensor_status).items()}
        s.last_packet_time = now
        if p.gnss_fix >= 2:
            if self.launch_position is None and p.system_status <= int(FlightState.ARMED):
                self.launch_position = (p.latitude, p.longitude)
            self.track.append((p.timestamp_ms / 1000.0, p.latitude, p.longitude, p.altitude))

    def link_status(self) -> str:
        if self.status.last_packet_time is None:
            return "NO LINK"
        age = self.now - self.status.last_packet_time
        return "LINK OK" if age <= self.rx.link_timeout else f"LINK LOST ({age:.0f} s)"

    def _en(self, lat: float, lon: float) -> tuple[float, float]:
        lat0, lon0 = self.launch_position or (lat, lon)
        return (math.radians(lon - lon0) * EARTH_R * math.cos(math.radians(lat0)),
                math.radians(lat - lat0) * EARTH_R)

    def landing_estimate(self) -> dict[str, float | str] | None:
        """Extrapolate current horizontal drift to the ground during DESCENT."""
        if self.status.state != "DESCENT" or len(self.track) < 5 or not self.status.velocity_mps:
            return None
        (t0, la0, lo0, _), (t1, la1, lo1, alt) = self.track[-5], self.track[-1]
        if t1 <= t0 or self.status.velocity_mps >= -0.5:
            return None
        e0, n0 = self._en(la0, lo0)
        e1, n1 = self._en(la1, lo1)
        ve, vn = (e1 - e0) / (t1 - t0), (n1 - n0) / (t1 - t0)
        t_ground = alt / -self.status.velocity_mps
        label, sigma = gnss_quality(self.status.gnss_fix, self.status.gnss_sats)
        # uncertainty grows with extrapolation time (wind changes with altitude)
        radius = 2 * sigma + 0.3 * t_ground * math.hypot(ve, vn) + 10.0
        return {"east_m": e1 + ve * t_ground, "north_m": n1 + vn * t_ground,
                "radius_m": radius, "time_to_ground_s": t_ground, "gnss_quality": label,
                "kind": "ESTIMATED"}

    def render(self) -> str:
        s = self.status
        label, sigma = gnss_quality(s.gnss_fix, s.gnss_sats)

        def f(x, fmt):
            return "—" if x is None else format(x, fmt)

        lines = [
            "AERODYNE GROUND",
            f"  link: {self.link_status()}   packets: {self.rx.stats.frames_ok}  "
            f"lost: {self.rx.stats.lost}  crc: {self.rx.stats.crc_failures}  "
            f"dup: {self.rx.stats.duplicates}  late: {self.rx.stats.out_of_order}",
            f"  state: {s.state}",
            f"  altitude: {f(s.altitude_m, '.0f')} m   (max {s.max_altitude_m:.0f} m)",
            f"  velocity: {f(s.velocity_mps, '.1f')} m/s   accel: {f(s.acceleration_mps2, '.1f')} m/s²",
            f"  battery: {f(s.battery_v, '.2f')} V   temp: {f(s.temperature_c, '.1f')} °C",
            f"  GPS: {label} ({s.gnss_sats} sats, ±{sigma:.0f} m)" if math.isfinite(sigma)
            else "  GPS: NO FIX",
        ]
        if self.track and self.launch_position and math.isfinite(sigma):
            e, n = self._en(self.track[-1][1], self.track[-1][2])
            step = 10 if sigma > 5 else 1       # never show more precision than the fix supports
            lines.append(f"  position: {round(e / step) * step:.0f} m E, "
                         f"{round(n / step) * step:.0f} m N of pad")
        est = self.landing_estimate()
        if est:
            lines.append(f"  est. landing: {est['east_m']:.0f} m E, {est['north_m']:.0f} m N "
                         f"(±{est['radius_m']:.0f} m, {est['time_to_ground_s']:.0f} s) [ESTIMATED]")
        bad = [k for k, v in s.sensor_health.items() if v not in ("OK",)]
        lines.append("  sensors: " + ("all OK" if not bad else
                                      ", ".join(f"{k}={s.sensor_health[k]}" for k in bad)))
        return "\n".join(lines)
