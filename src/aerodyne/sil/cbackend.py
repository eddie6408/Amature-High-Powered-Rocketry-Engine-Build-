"""ctypes bindings to the compiled C flight-software core
(``firmware/build/libaerodyne_fsw.so``) so SIL runs the *actual* flight code.

Build it with::

    cmake -S firmware -B firmware/build && cmake --build firmware/build

or point ``AERODYNE_FSW_LIB`` at a library built elsewhere.
"""

from __future__ import annotations

import ctypes as C
import math
import os
from pathlib import Path

from aerodyne.avionics.state_machine import FlightState, FsmConfig, FsmInputs, Transition
from aerodyne.avionics.telemetry import TelemetryPacket

_REASONS = {
    0: "none", 1: "command", 2: "liftoff evidence: accel", 3: "liftoff evidence: baro",
    4: "liftoff evidence: accel+baro", 5: "burnout: axial deceleration",
    6: "burnout: max burn time elapsed", 7: "burnout: accel unavailable, velocity falling",
    8: "apogee: velocity<0 and baro drop", 9: "apogee: velocity<0 (baro degraded)",
    10: "landed: stationary near ground (baro/filter)",
    11: "landed: stationary near ground (GNSS, baro degraded)",
    12: "landed: timeout after apogee",
}
_CMDS = {None: 0, "preflight": 1, "arm": 2, "disarm": 3, "safe": 4}


class _Cfg(C.Structure):
    _fields_ = [(n, C.c_float) for n in (
        "launch_accel", "launch_debounce", "launch_baro_alt", "launch_baro_debounce",
        "burnout_accel", "burnout_debounce", "max_burn_time", "apogee_lockout",
        "apogee_vel_debounce", "apogee_baro_drop", "apogee_baro_debounce", "mach_lockout_speed",
        "landed_speed", "landed_debounce", "landed_alt_band", "landed_timeout")]


class _Inputs(C.Structure):
    _fields_ = [("t", C.c_double), ("accel_axial", C.c_float), ("accel_ok", C.c_bool),
                ("baro_alt_agl", C.c_float), ("baro_ok", C.c_bool), ("est_alt_agl", C.c_float),
                ("est_vel", C.c_float), ("gnss_ok", C.c_bool), ("gnss_alt_agl", C.c_float),
                ("gnss_vel", C.c_float), ("command", C.c_int), ("preflight_ok", C.c_bool)]


class _Fsm(C.Structure):
    _fields_ = [("cfg", _Cfg), ("state", C.c_int), ("t_liftoff", C.c_double),
                ("t_apogee", C.c_double), ("max_baro_alt", C.c_float),
                ("timer_start", C.c_double * 7), ("timer_running", C.c_bool * 7),
                ("last_reason", C.c_int), ("transition_count", C.c_uint32)]


class _Kf(C.Structure):
    _fields_ = [("x", C.c_double * 2), ("P", (C.c_double * 2) * 2), ("accel_sigma", C.c_double),
                ("baro_sigma", C.c_double)]


class _Tlm(C.Structure):
    _fields_ = [("vehicle_id", C.c_uint16), ("flight_id", C.c_uint16), ("sequence", C.c_uint32),
                ("timestamp_ms", C.c_uint32), ("altitude", C.c_float), ("velocity", C.c_float),
                ("acceleration", C.c_float), ("lat_e7", C.c_int32), ("lon_e7", C.c_int32),
                ("attitude", C.c_int16 * 4), ("battery_mv", C.c_uint16),
                ("temperature_c10", C.c_int16), ("system_status", C.c_uint8),
                ("sensor_status", C.c_uint16), ("nav_status", C.c_uint8), ("gnss_fix", C.c_uint8),
                ("gnss_sats", C.c_uint8)]


def library_path() -> Path:
    env = os.environ.get("AERODYNE_FSW_LIB")
    if env:
        return Path(env)
    root = Path(__file__).resolve().parents[3]
    return root / "firmware" / "build" / "libaerodyne_fsw.so"


_lib: C.CDLL | None = None


def load() -> C.CDLL:
    global _lib
    if _lib is None:
        path = library_path()
        if not path.exists():
            raise FileNotFoundError(f"C flight software library not built: {path}")
        lib = C.CDLL(str(path))
        lib.aero_fsm_init.argtypes = [C.POINTER(_Fsm), C.POINTER(_Cfg)]
        lib.aero_fsm_step.argtypes = [C.POINTER(_Fsm), C.POINTER(_Inputs)]
        lib.aero_fsm_step.restype = C.c_int
        lib.aero_fsm_restore.argtypes = [C.POINTER(_Fsm), C.c_int, C.c_double, C.c_float, C.c_double]
        lib.aero_kf_init.argtypes = [C.POINTER(_Kf), C.c_double, C.c_double, C.c_double]
        lib.aero_kf_predict.argtypes = [C.POINTER(_Kf), C.c_double, C.c_double, C.c_double]
        lib.aero_kf_update.argtypes = [C.POINTER(_Kf), C.c_double, C.c_double]
        lib.aero_kf_update.restype = C.c_double
        lib.aero_kf_innovation.argtypes = [C.POINTER(_Kf), C.c_double, C.c_double]
        lib.aero_kf_innovation.restype = C.c_double
        lib.aero_kf_set_state.argtypes = [C.POINTER(_Kf), C.c_double, C.c_double]
        lib.aero_tlm_encode.argtypes = [C.POINTER(_Tlm), C.POINTER(C.c_uint8)]
        lib.aero_tlm_encode.restype = C.c_size_t
        lib.aero_crc16_ccitt.argtypes = [C.POINTER(C.c_uint8), C.c_size_t, C.c_uint16]
        lib.aero_crc16_ccitt.restype = C.c_uint16
        lib.aero_fw_version.restype = C.c_char_p
        lib.aero_fw_commit.restype = C.c_char_p
        _lib = lib
    return _lib


def available() -> bool:
    try:
        load()
        return True
    except (FileNotFoundError, OSError):
        return False


def _nan_to_none(x: float) -> float | None:
    return None if math.isnan(x) else x


class CFlightStateMachine:
    """Same interface as :class:`aerodyne.avionics.state_machine.FlightStateMachine`."""

    def __init__(self, cfg: FsmConfig | None = None) -> None:
        self.lib = load()
        cfg = cfg or FsmConfig()
        self._c = _Fsm()
        ccfg = _Cfg(**{f: getattr(cfg, f) for f, _ in _Cfg._fields_})
        self.lib.aero_fsm_init(C.byref(self._c), C.byref(ccfg))
        self.transitions: list[Transition] = []

    @property
    def state(self) -> FlightState:
        return FlightState(self._c.state)

    @property
    def t_liftoff(self) -> float | None:
        return _nan_to_none(self._c.t_liftoff)

    @property
    def t_apogee(self) -> float | None:
        return _nan_to_none(self._c.t_apogee)

    @property
    def max_baro_alt(self) -> float:
        return float(self._c.max_baro_alt)

    def restore(self, state: FlightState, t_liftoff: float | None, max_baro_alt: float,
                t_apogee: float | None = None) -> None:
        self.lib.aero_fsm_restore(C.byref(self._c), int(state),
                                  math.nan if t_liftoff is None else t_liftoff, max_baro_alt,
                                  math.nan if t_apogee is None else t_apogee)

    def step(self, i: FsmInputs) -> FlightState:
        before = self.state
        ci = _Inputs(t=i.t, accel_axial=i.accel_axial, accel_ok=i.accel_ok,
                     baro_alt_agl=i.baro_alt_agl, baro_ok=i.baro_ok, est_alt_agl=i.est_alt_agl,
                     est_vel=i.est_vel, gnss_ok=i.gnss_ok, gnss_alt_agl=i.gnss_alt_agl,
                     gnss_vel=i.gnss_vel, command=_CMDS.get(i.command, 0),
                     preflight_ok=i.preflight_ok)
        self.lib.aero_fsm_step(C.byref(self._c), C.byref(ci))
        after = self.state
        if after != before:
            self.transitions.append(Transition(i.t, before, after,
                                               _REASONS.get(self._c.last_reason, "?")))
        return after


class CAltitudeKalmanFilter:
    def __init__(self, accel_sigma: float = 2.0, baro_sigma: float = 1.5,
                 initial_altitude: float = 0.0) -> None:
        self.lib = load()
        self._c = _Kf()
        self.lib.aero_kf_init(C.byref(self._c), accel_sigma, baro_sigma, initial_altitude)

    def predict(self, a_vertical: float, dt: float, accel_sigma: float | None = None) -> None:
        self.lib.aero_kf_predict(C.byref(self._c), a_vertical, dt,
                                 -1.0 if accel_sigma is None else accel_sigma)

    def update(self, baro_altitude: float, baro_sigma: float | None = None) -> float:
        return self.lib.aero_kf_update(C.byref(self._c), baro_altitude,
                                       -1.0 if baro_sigma is None else baro_sigma)

    def innovation(self, baro_altitude: float, baro_sigma: float | None = None) -> float:
        return self.lib.aero_kf_innovation(C.byref(self._c), baro_altitude,
                                           -1.0 if baro_sigma is None else baro_sigma)

    def set_state(self, altitude: float, velocity: float) -> None:
        self.lib.aero_kf_set_state(C.byref(self._c), altitude, velocity)

    @property
    def altitude(self) -> float:
        return float(self._c.x[0])

    @property
    def velocity(self) -> float:
        return float(self._c.x[1])


def c_encode(p: TelemetryPacket) -> bytes:
    lib = load()
    t = _Tlm(vehicle_id=p.vehicle_id, flight_id=p.flight_id, sequence=p.sequence,
             timestamp_ms=p.timestamp_ms, altitude=p.altitude, velocity=p.velocity,
             acceleration=p.acceleration, lat_e7=round(p.latitude * 1e7),
             lon_e7=round(p.longitude * 1e7),
             attitude=(C.c_int16 * 4)(*[max(-32767, min(32767, round(c * 32767)))
                                        for c in p.attitude]),
             battery_mv=p.battery_mv,
             temperature_c10=max(-32768, min(32767, round(p.temperature_c * 10))),
             system_status=p.system_status, sensor_status=p.sensor_status,
             nav_status=p.nav_status, gnss_fix=p.gnss_fix, gnss_sats=p.gnss_sats)
    buf = (C.c_uint8 * 64)()
    n = lib.aero_tlm_encode(C.byref(t), buf)
    return bytes(buf[:n])


def firmware_version() -> tuple[str, str]:
    lib = load()
    return lib.aero_fw_version().decode(), lib.aero_fw_commit().decode()
