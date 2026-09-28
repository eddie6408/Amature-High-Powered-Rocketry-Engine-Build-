"""Flight software application (reference implementation).

Runs one cycle per call to :meth:`FlightSoftware.step`: validate sensors ->
log raw data -> propagate attitude -> Kalman filter -> state machine ->
health monitor -> telemetry. The same structure is implemented in C as
FreeRTOS tasks (see ``firmware/`` and ``docs/FIRMWARE.md``); this module can
drive either the Python or the compiled C state machine / filter.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from aerodyne.avionics.estimation import AltitudeKalmanFilter, AttitudePropagator
from aerodyne.avionics.firmware import FirmwareIdentity
from aerodyne.avionics.logger import FlightDataLogger, StorageError
from aerodyne.avionics.sensors import (
    DEFAULT_LIMITS,
    Health,
    SensorReading,
    SensorValidator,
    ValidationLimits,
)
from aerodyne.avionics.state_machine import (
    G,
    IN_FLIGHT,
    FlightState,
    FlightStateMachine,
    FsmConfig,
    FsmInputs,
)
from aerodyne.avionics.telemetry import TelemetryPacket, pack_sensor_status
from aerodyne.dynamics import quaternion as quat


@dataclass
class FswConfig:
    vehicle_id: int = 1
    flight_id: int = 1
    telemetry_period: float = 0.1
    low_battery_v: float = 7.0
    fsm: FsmConfig = field(default_factory=FsmConfig)
    limits: dict[str, ValidationLimits] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    baro_gate_sigma: float = 6.0            # innovation gate for baro updates
    descent_accel_sigma: float = 8.0         # process noise when accel is not usable
    identity: FirmwareIdentity | None = None


class FlightSoftware:
    def __init__(self, cfg: FswConfig | None = None, logger: FlightDataLogger | None = None,
                 nv_store: dict[str, Any] | None = None, backend: str = "python") -> None:
        self.cfg = cfg or FswConfig()
        self.log = logger or FlightDataLogger()
        self.nv = nv_store if nv_store is not None else {}
        self.backend = backend
        if backend == "c":
            from aerodyne.sil.cbackend import CAltitudeKalmanFilter, CFlightStateMachine

            self.fsm = CFlightStateMachine(self.cfg.fsm)
            self.kf = CAltitudeKalmanFilter()
        else:
            self.fsm = FlightStateMachine(self.cfg.fsm)
            self.kf = AltitudeKalmanFilter()
        self.att = AttitudePropagator()
        self.validators = {k: SensorValidator(v) for k, v in self.cfg.limits.items()}
        self.health: dict[str, Health] = {k: Health.UNKNOWN for k in self.validators}
        self.last_sample: dict[str, float] = {}
        self.latest: dict[str, SensorReading] = {}
        self.ground_alt: float | None = None
        self.t_prev: float | None = None
        self.next_tlm = 0.0
        self.sequence = int(self.nv.get("tlm_sequence", 0))
        self.faults: set[str] = set()
        self.max_est_alt = 0.0
        self.pending_command: str | None = None
        self.preflight_ok = False
        self.boot_count = int(self.nv.get("boot_count", 0)) + 1
        self.nv["boot_count"] = self.boot_count
        self._leveled = False
        self.baro_agl = 0.0
        self.baro_rejects = 0
        self.gnss_ground: float | None = None
        self._gnss_hist: deque[tuple[float, float]] = deque()
        self._restore()

    # ---- persistence (backup registers / FRAM on real hardware) --------------
    def _persist(self) -> None:
        self.nv.update(state=int(self.fsm.state), t_liftoff=self.fsm.t_liftoff,
                       t_apogee=self.fsm.t_apogee, gnss_ground=self.gnss_ground,
                       max_baro_alt=self.fsm.max_baro_alt, ground_alt=self.ground_alt,
                       kf_alt=self.kf.altitude, kf_vel=self.kf.velocity,
                       attitude=list(self.att.q))

    def _restore(self) -> None:
        if "state" not in self.nv:
            return
        st = FlightState(self.nv["state"])
        if st in IN_FLIGHT or st == FlightState.LANDED:
            self.fsm.restore(st, self.nv.get("t_liftoff"), self.nv.get("max_baro_alt", -1e9),
                             self.nv.get("t_apogee"))
            self.ground_alt = self.nv.get("ground_alt")
            self.gnss_ground = self.nv.get("gnss_ground")
            self.kf.set_state(self.nv.get("kf_alt", 0.0), self.nv.get("kf_vel", 0.0))
            if "attitude" in self.nv:
                self.att.q = np.array(self.nv["attitude"])
                self._leveled = True
            self._event(0.0, "RESET_RECOVERY", f"resumed in {st.name} (boot {self.boot_count})")

    # ---- helpers ----------------------------------------------------------------
    def _event(self, t: float, name: str, detail: str = "") -> None:
        try:
            self.log.log_event(t, name, detail)
        except StorageError:
            self._storage_fault(t)

    def _storage_fault(self, t: float) -> None:
        if "storage" not in self.faults:
            self.faults.add("storage")

    def command(self, cmd: str, preflight_ok: bool = False) -> None:
        self.pending_command = cmd
        self.preflight_ok = preflight_ok

    def usable(self, name: str) -> bool:
        return self.health.get(name) == Health.OK and name in self.latest

    # ---- main cycle ----------------------------------------------------------------
    def step(self, t: float, readings: dict[str, SensorReading]) -> TelemetryPacket | None:
        dt = 0.0 if self.t_prev is None else max(t - self.t_prev, 0.0)
        self.t_prev = t

        # 1. validate + log raw
        fresh: dict[str, SensorReading] = {}
        for name, v in self.validators.items():
            r = readings.get(name)
            if r is None:
                last = self.last_sample.get(name)
                if last is not None and t - last > v.lim.stale_timeout:
                    res = v.check(None, t)
                    self.health[name] = res.health
                    self.latest.pop(name, None)
                continue
            res = v.check(r, t)
            self.health[name] = res.health
            self.last_sample[name] = t
            try:
                self.log.log_raw(r.timestamp, r.sensor_id, r.value, r.status.name, res.health.name)
            except StorageError:
                self._storage_fault(t)
            if res.ok:
                fresh[name] = r
                self.latest[name] = r
            elif res.health != Health.OK:
                self.latest.pop(name, None)

        state = self.fsm.state
        accel_ok = "imu_accel" in fresh and self.health["imu_accel"] == Health.OK
        gyro_ok = "imu_gyro" in fresh and self.health["imu_gyro"] == Health.OK
        baro_ok = "baro" in fresh and self.health["baro"] == Health.OK

        # 2. ground reference and attitude leveling while on the pad
        if state in (FlightState.SAFE, FlightState.PREFLIGHT, FlightState.ARMED):
            if baro_ok:
                b = float(fresh["baro"].value)
                self.ground_alt = b if self.ground_alt is None else 0.98 * self.ground_alt + 0.02 * b
                self.kf.set_state(0.0, 0.0)
            if accel_ok:
                f = np.asarray(fresh["imu_accel"].value, dtype=float)
                if np.linalg.norm(f) > 0.5 * G:
                    self.att.q = quat.from_two_vectors(f, np.array([0.0, 0.0, 1.0]))
                    self._leveled = True
        elif gyro_ok and dt > 0:
            self.att.propagate(np.asarray(fresh["imu_gyro"].value, dtype=float), dt)

        # 3. navigation
        accel_axial = float(np.asarray(fresh["imu_accel"].value)[0]) if accel_ok else 0.0
        if dt > 0:
            if accel_ok and state not in (FlightState.DESCENT, FlightState.LANDED):
                f_body = np.asarray(fresh["imu_accel"].value, dtype=float)
                f_world = quat.to_matrix(self.att.q) @ f_body if self._leveled else f_body
                self.kf.predict(float(f_world[2]) - G, dt)
            else:
                self.kf.predict(0.0, dt, accel_sigma=self.cfg.descent_accel_sigma)
        if baro_ok and self.ground_alt is not None:
            baro_agl = float(fresh["baro"].value) - self.ground_alt
            transonic = abs(self.kf.velocity) > self.cfg.fsm.mach_lockout_speed
            nis = self.kf.innovation(baro_agl)
            gated = abs(nis) >= self.cfg.baro_gate_sigma and accel_ok and state in IN_FLIGHT
            if gated:
                self.baro_rejects += 1
            else:
                # an accepted sample becomes the baro evidence the FSM sees; a gated
                # sample is discarded and the previous accepted value is held
                self.baro_agl = baro_agl
                if not transonic:
                    self.kf.update(baro_agl)
        self.max_est_alt = max(self.max_est_alt, self.kf.altitude)
        # FSM evidence: a source "votes" only while its sensor is healthy
        baro_healthy = self.health.get("baro") == Health.OK and self.ground_alt is not None
        accel_healthy = self.health.get("imu_accel") == Health.OK and "imu_accel" in self.latest
        if accel_healthy and not accel_ok:
            accel_axial = float(np.asarray(self.latest["imu_accel"].value)[0])

        # GNSS altitude (independent vertical source for landing detection)
        gnss_ok = "gnss" in fresh and self.health.get("gnss") == Health.OK
        gnss_agl, gnss_vel = 0.0, 0.0
        if gnss_ok:
            galt = float(fresh["gnss"].value[2])
            if state in (FlightState.SAFE, FlightState.PREFLIGHT, FlightState.ARMED):
                self.gnss_ground = galt if self.gnss_ground is None else 0.9 * self.gnss_ground + 0.1 * galt
            self._gnss_hist.append((t, galt))
        while self._gnss_hist and t - self._gnss_hist[0][0] > 5.0:
            self._gnss_hist.popleft()
        gnss_valid = (self.health.get("gnss") == Health.OK and self.gnss_ground is not None
                      and len(self._gnss_hist) >= 2 and self._gnss_hist[-1][0] - self._gnss_hist[0][0] >= 4.0)
        if gnss_valid:
            # least-squares slope over every fix in the window: an end-point difference
            # of noisy fixes can exceed the "stationary" threshold at rest
            ts = [x[0] for x in self._gnss_hist]
            al = [x[1] for x in self._gnss_hist]
            tm, am = sum(ts) / len(ts), sum(al) / len(al)
            sxx = sum((x - tm) ** 2 for x in ts)
            gnss_vel = sum((x - tm) * (a - am) for x, a in zip(ts, al)) / sxx
            gnss_agl = al[-1] - self.gnss_ground

        # 4. state machine
        prev = self.fsm.state
        new = self.fsm.step(FsmInputs(
            t=t, accel_axial=accel_axial, accel_ok=accel_healthy, baro_alt_agl=self.baro_agl,
            baro_ok=baro_healthy, est_alt_agl=self.kf.altitude, est_vel=self.kf.velocity,
            gnss_ok=gnss_valid, gnss_alt_agl=gnss_agl, gnss_vel=gnss_vel,
            command=self.pending_command, preflight_ok=self.preflight_ok))
        self.pending_command = None
        if new != prev:
            tr = self.fsm.transitions[-1]
            self._event(t, f"STATE {prev.name}->{new.name}", tr.reason)
            self._persist()
        elif new in IN_FLIGHT and int(t * 10) != int((t - dt) * 10):
            self._persist()

        # 5. health monitor
        batt = self.latest.get("battery")
        if batt is not None and float(batt.value) < self.cfg.low_battery_v and "low_battery" not in self.faults:
            self.faults.add("low_battery")
            self._event(t, "LOW_BATTERY", f"{float(batt.value):.2f} V")
        for name, h in self.health.items():
            key = f"sensor:{name}"
            if h in (Health.DEGRADED, Health.FAILED) and key not in self.faults:
                self.faults.add(key)
                self._event(t, "SENSOR_DEGRADED", f"{name} {h.name}")

        try:
            self.log.log_estimate(t, alt=self.kf.altitude, vel=self.kf.velocity,
                                  state=int(self.fsm.state))
        except StorageError:
            self._storage_fault(t)

        # 6. telemetry
        if t + 1e-9 >= self.next_tlm:
            self.next_tlm = t + self.cfg.telemetry_period
            return self._packet(t)
        return None

    def _packet(self, t: float) -> TelemetryPacket:
        self.sequence += 1
        self.nv["tlm_sequence"] = self.sequence
        g = self.latest.get("gnss")
        lat, lon, fix, sats = 0.0, 0.0, 0, 0
        if g is not None and self.health.get("gnss") == Health.OK:
            lat, lon = g.value[0], g.value[1]
            fix, sats = 3, int(round(g.quality * 12))
        batt = self.latest.get("battery")
        temp = self.latest.get("temperature")
        status = {k: int(v) for k, v in self.health.items()}
        status["storage"] = int(Health.FAILED if "storage" in self.faults else Health.OK)
        status["radio"] = int(Health.OK)
        nav = (1 if self.usable("baro") else 0) | (2 if self.usable("imu_accel") else 0) | (
            4 if fix else 0)
        acc = self.latest.get("imu_accel")
        return TelemetryPacket(
            vehicle_id=self.cfg.vehicle_id, flight_id=self.cfg.flight_id, sequence=self.sequence,
            timestamp_ms=int(t * 1000) & 0xFFFFFFFF, altitude=self.kf.altitude,
            velocity=self.kf.velocity,
            acceleration=float(np.asarray(acc.value)[0]) if acc is not None else math.nan,
            latitude=lat, longitude=lon, attitude=tuple(float(x) for x in self.att.q),
            battery_mv=int(float(batt.value) * 1000) if batt else 0,
            temperature_c=float(temp.value) if temp else 0.0, system_status=int(self.fsm.state),
            sensor_status=pack_sensor_status(status), nav_status=nav, gnss_fix=fix, gnss_sats=sats)
