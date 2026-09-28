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
    nav_status: int = 0
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


@dataclass(frozen=True)
class DescentPlan:
    """Planned recovery profile (from the digital twin's recovery config)."""

    main_deploy_alt_agl: float
    main_descent_rate: float        # m/s, positive

    @classmethod
    def from_recovery(cls, config, mass: float, density: float = 1.2) -> "DescentPlan | None":
        from aerodyne.recovery.recovery import descent_rate

        mains = [d for d in config.devices if d.deploy_event == "altitude" and d.deploy_altitude_agl]
        if not mains:
            return None
        main = mains[0]
        cda = config.body_cd_area + sum(d.cd_area for d in config.devices)
        return cls(main.deploy_altitude_agl, descent_rate(mass, cda, density))


class GroundStation:
    def __init__(self, vehicle_id: int | None = None, link_timeout: float = 2.0,
                 descent_plan: DescentPlan | None = None) -> None:
        self.plan = descent_plan
        self.rx = TelemetryReceiver(vehicle_id=vehicle_id, link_timeout=link_timeout)
        self.status = VehicleStatus()
        self.launch_position: tuple[float, float] | None = None
        self.track: list[tuple[float, float, float, float]] = []    # (t, lat, lon, alt)
        self.history: list[TelemetryPacket] = []
        self.now = 0.0
        self.descent_t0: float | None = None     # vehicle time of the first DESCENT packet
        self.estimate_note = ""

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
        s.nav_status = p.nav_status
        s.sensor_health = {k: Health(v).name for k, v in unpack_sensor_status(p.sensor_status).items()}
        s.last_packet_time = now
        if s.state == "DESCENT" and self.descent_t0 is None:
            self.descent_t0 = p.timestamp_ms / 1000.0
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

    def landing_estimate(self, window: float = 5.0) -> dict[str, float | str] | None:
        """Extrapolate the recent horizontal drift to the ground during DESCENT.

        Drift velocity is a least-squares fit over the last ``window`` seconds of
        GNSS track; the radius combines GNSS noise propagated through the fit and
        extrapolation with an allowance for wind changing with altitude."""
        self.estimate_note = ""
        if self.status.state != "DESCENT" or not self.track or not self.status.velocity_mps:
            return None
        if not self.status.nav_status & 1:
            # altitude/velocity are not barometer-aided (baro degraded): an inertial-only
            # estimate drifts without bound, so no time-to-ground can be trusted
            self.estimate_note = "landing estimate unavailable: altitude not baro-aided"
            return None
        if self.status.velocity_mps >= -0.5:
            return None
        t_last = self.track[-1][0]
        # drift is fitted only once descent has settled: fixes from the coast and the
        # first seconds under the drogue describe the ascent, not the wind drift
        settle = (self.descent_t0 or t_last) + 2.0
        pts = [p for p in self.track if t_last - p[0] <= window and p[0] >= settle]
        if len(pts) < 10 or pts[-1][0] - pts[0][0] < 0.6 * window:
            return None
        ts = [p[0] for p in pts]
        en = [self._en(p[1], p[2]) for p in pts]
        tm = sum(ts) / len(ts)
        sxx = sum((t - tm) ** 2 for t in ts)
        ve = sum((t - tm) * e for t, (e, _) in zip(ts, en)) / sxx
        vn = sum((t - tm) * n for t, (_, n) in zip(ts, en)) / sxx
        e_now = sum(e for e, _ in en) / len(en) + ve * (t_last - tm)
        n_now = sum(n for _, n in en) / len(en) + vn * (t_last - tm)
        alt = max(self.track[-1][3], 0.0)
        v_now = -self.status.velocity_mps
        basis = "current descent rate"
        if self.plan and v_now > 1.5 * self.plan.main_descent_rate:
            # main not yet open (or still inflating): fast down to the deploy altitude,
            # then the planned main rate
            h = self.plan.main_deploy_alt_agl
            t_ground = max(alt - h, 0.0) / v_now + min(alt, h) / self.plan.main_descent_rate
            t_spread = 0.25 * h / self.plan.main_descent_rate    # main-rate uncertainty
            basis = "descent plan"
        elif self.plan is None:
            # no plan: the vehicle may still slow down (main opening/inflating) - bracket
            # between the current rate and the slowest plausible main canopy
            t_fast, t_slow = alt / v_now, alt / min(v_now, 3.0)
            t_ground, t_spread = 0.5 * (t_fast + t_slow), 0.5 * (t_slow - t_fast) + 0.1 * t_fast
            basis = "PRELIMINARY (no descent plan)" if v_now > 8.0 else "current descent rate, no plan"
        else:
            t_ground, t_spread = alt / v_now, 0.1 * alt / v_now
        label, sigma = gnss_quality(self.status.gnss_fix, self.status.gnss_sats)
        sigma_v = sigma / math.sqrt(sxx)                 # 1-sigma of fitted drift speed
        drift = math.hypot(ve, vn)
        radius = (2.0 * math.hypot(sigma, sigma_v * t_ground) + drift * t_spread
                  + 0.3 * t_ground * drift + 10.0)
        return {"east_m": e_now + ve * t_ground, "north_m": n_now + vn * t_ground,
                "radius_m": radius, "time_to_ground_s": t_ground, "gnss_quality": label,
                "basis": basis, "kind": "ESTIMATED"}

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
            f"  altitude: {f(None if s.altitude_m is None else s.altitude_m + 0.0, '.0f')} m   "
            f"(max {s.max_altitude_m:.0f} m)",
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
                         f"(±{est['radius_m']:.0f} m, {est['time_to_ground_s']:.0f} s, {est['basis']}) [ESTIMATED]")
        if self.estimate_note:
            lines.append(f"  {self.estimate_note}")
        bad = [k for k, v in s.sensor_health.items() if v not in ("OK",)]
        lines.append("  sensors: " + ("all OK" if not bad else
                                      ", ".join(f"{k}={s.sensor_health[k]}" for k in bad)))
        return "\n".join(lines)
