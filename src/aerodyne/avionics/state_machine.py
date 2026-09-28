"""Flight state machine (reference implementation of firmware/src/flight_state.c).

SAFE -> PREFLIGHT -> ARMED -> ASCENT -> COAST -> DESCENT -> LANDED

Rules:
* ground transitions (SAFE/PREFLIGHT/ARMED) happen only on explicit commands;
* in-flight transitions need *sustained* evidence (debounce) - a single
  anomalous sample can never cause a transition;
* apogee needs agreement of the independent evidence sources that are healthy
  (filtered vertical velocity, barometric altitude drop) plus a time lockout,
  and barometric evidence is ignored while the estimated speed is transonic;
* there is no path from any flight state back to a ground state; a processor
  reset resumes from the persisted state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

G = 9.80665


class FlightState(IntEnum):
    SAFE = 0
    PREFLIGHT = 1
    ARMED = 2
    ASCENT = 3
    COAST = 4
    DESCENT = 5
    LANDED = 6


IN_FLIGHT = {FlightState.ASCENT, FlightState.COAST, FlightState.DESCENT}


@dataclass(frozen=True)
class FsmConfig:
    launch_accel: float = 2.0 * G          # m/s^2 axial specific force
    launch_debounce: float = 0.10          # s
    launch_baro_alt: float = 25.0          # m above pad (backup evidence)
    launch_baro_debounce: float = 0.30
    burnout_accel: float = 0.0             # m/s^2; below this = motor no longer thrusting
    burnout_debounce: float = 0.10
    max_burn_time: float = 10.0            # s after liftoff: forced COAST
    apogee_lockout: float = 3.0            # s after liftoff before apogee may be declared
    apogee_vel_debounce: float = 0.20
    apogee_baro_drop: float = 3.0          # m below peak baro altitude
    apogee_baro_debounce: float = 0.20
    mach_lockout_speed: float = 240.0      # m/s; baro evidence ignored above this
    landed_speed: float = 2.0              # m/s |v| considered stationary
    landed_debounce: float = 5.0
    landed_alt_band: float = 50.0          # m AGL; must be near the ground
    landed_timeout: float = 900.0          # s after apogee: declare LANDED regardless


@dataclass
class FsmInputs:
    t: float
    accel_axial: float             # m/s^2 specific force along body x
    accel_ok: bool
    baro_alt_agl: float            # m above pad (raw baro altitude minus ground reference)
    baro_ok: bool
    est_alt_agl: float             # filtered altitude
    est_vel: float                 # filtered vertical velocity
    gnss_ok: bool = False
    gnss_alt_agl: float = 0.0
    gnss_vel: float = 0.0          # vertical speed from GNSS altitude over a 5 s window
    command: str | None = None     # "preflight" | "arm" | "disarm" | "safe"
    preflight_ok: bool = False


@dataclass
class Transition:
    t: float
    from_state: FlightState
    to_state: FlightState
    reason: str


@dataclass
class FlightStateMachine:
    cfg: FsmConfig = field(default_factory=FsmConfig)
    state: FlightState = FlightState.SAFE
    transitions: list[Transition] = field(default_factory=list)
    t_liftoff: float | None = None
    t_apogee: float | None = None
    max_baro_alt: float = -1e9
    _timers: dict[str, float | None] = field(default_factory=dict)

    # --- helpers -----------------------------------------------------------
    def _sustained(self, key: str, cond: bool, t: float, duration: float) -> bool:
        if not cond:
            self._timers[key] = None
            return False
        start = self._timers.get(key)
        if start is None:
            self._timers[key] = t
            return duration <= 0
        return t - start >= duration - 1e-6   # same 1 us tolerance as the C implementation

    def _go(self, t: float, new: FlightState, reason: str) -> None:
        self.transitions.append(Transition(t, self.state, new, reason))
        self.state = new
        self._timers.clear()

    def restore(self, state: FlightState, t_liftoff: float | None, max_baro_alt: float,
                t_apogee: float | None = None) -> None:
        """Resume after a processor reset from persisted non-volatile state."""
        self.state = state
        self.t_liftoff = t_liftoff
        self.t_apogee = t_apogee
        self.max_baro_alt = max_baro_alt
        self._timers.clear()

    # --- step ----------------------------------------------------------------
    def step(self, i: FsmInputs) -> FlightState:
        c = self.cfg
        s = self.state
        if i.baro_ok and s in IN_FLIGHT:
            self.max_baro_alt = max(self.max_baro_alt, i.baro_alt_agl)

        if s == FlightState.SAFE:
            if i.command == "preflight":
                self._go(i.t, FlightState.PREFLIGHT, "command")
        elif s == FlightState.PREFLIGHT:
            if i.command == "arm":
                if i.preflight_ok:
                    self._go(i.t, FlightState.ARMED, "command; preflight passed")
            elif i.command in ("safe", "disarm"):
                self._go(i.t, FlightState.SAFE, "command")
        elif s == FlightState.ARMED:
            if i.command in ("disarm", "safe"):
                self._go(i.t, FlightState.SAFE, "command")
                return self.state
            accel_ev = self._sustained("launch_acc", i.accel_ok and i.accel_axial > c.launch_accel,
                                       i.t, c.launch_debounce)
            baro_ev = self._sustained("launch_baro", i.baro_ok and i.baro_alt_agl > c.launch_baro_alt,
                                      i.t, c.launch_baro_debounce)
            if accel_ev or baro_ev:
                why = "+".join(n for n, e in (("accel", accel_ev), ("baro", baro_ev)) if e)
                self.t_liftoff = i.t - (c.launch_debounce if accel_ev else c.launch_baro_debounce)
                self._go(i.t, FlightState.ASCENT, f"liftoff evidence: {why}")
        elif s == FlightState.ASCENT:
            since = i.t - (i.t if self.t_liftoff is None else self.t_liftoff)
            bo = self._sustained("burnout", i.accel_ok and i.accel_axial < c.burnout_accel,
                                 i.t, c.burnout_debounce)
            if bo:
                self._go(i.t, FlightState.COAST, "burnout: axial deceleration")
            elif since > c.max_burn_time:
                self._go(i.t, FlightState.COAST, "burnout: max burn time elapsed")
            elif not i.accel_ok and self._sustained("bo_vel", i.est_vel < 0, i.t, c.apogee_vel_debounce):
                self._go(i.t, FlightState.COAST, "burnout: accel unavailable, velocity falling")
        elif s == FlightState.COAST:
            since = i.t - (i.t if self.t_liftoff is None else self.t_liftoff)
            vel_ev = self._sustained("apo_vel", i.est_vel < 0.0, i.t, c.apogee_vel_debounce)
            baro_usable = i.baro_ok and abs(i.est_vel) < c.mach_lockout_speed
            baro_ev = self._sustained("apo_baro", baro_usable and
                                      i.baro_alt_agl < self.max_baro_alt - c.apogee_baro_drop,
                                      i.t, c.apogee_baro_debounce)
            if since >= c.apogee_lockout:
                if i.baro_ok:
                    ok = vel_ev and baro_ev      # two independent sources must agree
                    why = "velocity<0 and baro drop"
                else:
                    ok = vel_ev                  # degraded: inertial-only estimate
                    why = "velocity<0 (baro degraded)"
                if ok:
                    self.t_apogee = i.t
                    self._go(i.t, FlightState.DESCENT, f"apogee: {why}")
        elif s == FlightState.DESCENT:
            if i.baro_ok:
                still = abs(i.est_vel) < c.landed_speed and i.est_alt_agl < c.landed_alt_band
                why = "stationary near ground (baro/filter)"
            else:
                # baro unavailable: the inertial-only estimate cannot see the ground,
                # use GNSS altitude as the independent source
                still = (i.gnss_ok and abs(i.gnss_vel) < c.landed_speed
                         and i.gnss_alt_agl < 2 * c.landed_alt_band)
                why = "stationary near ground (GNSS, baro degraded)"
            if self._sustained("landed", still, i.t, c.landed_debounce):
                self._go(i.t, FlightState.LANDED, f"landed: {why}")
            elif self.t_apogee is not None and i.t - self.t_apogee > c.landed_timeout:
                self._go(i.t, FlightState.LANDED, "landed: timeout after apogee")
        return self.state
