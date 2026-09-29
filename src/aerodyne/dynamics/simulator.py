"""Six-degree-of-freedom flight simulator.

Frames
------
* World: local ENU (east, north, up), origin at the launch pad, flat Earth.
* Body: x along the vehicle axis toward the nose; y, z transverse.

Modelled: position, velocity, acceleration, attitude (quaternion), angular
velocity, time-varying mass / CG / inertia, altitude-dependent gravity,
atmosphere, wind, thrust, axial and normal aerodynamic forces, restoring
moment about the CG, fin pitch-damping moment, launch-rail constraint, and a
3-DOF drag-only descent under recovery devices.

Not modelled (documented limitations): Earth rotation/curvature, jet damping,
thrust misalignment, fin cant/roll dynamics, rail-button tip-off, body-lift at
large alpha. Results are SIMULATED and must be validated against flight data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from aerodyne.aero.model import AeroModel, FlightCondition, ScaledAeroModel
from aerodyne.core.provenance import DataKind
from aerodyne.dynamics import quaternion as quat
from aerodyne.dynamics.propulsion_system import PropulsionSystem
from aerodyne.environment.atmosphere import G0, AtmosphereModel, StandardAtmosphere
from aerodyne.environment.wind import ConstantWind, WindModel
from aerodyne.propulsion.motor import MotorPerformance
from aerodyne.recovery.recovery import RecoveryConfig
from aerodyne.vehicle.mass import MassPropertiesEngine
from aerodyne.vehicle.vehicle import Vehicle

EARTH_R = 6371000.0


@dataclass(frozen=True)
class LaunchSite:
    altitude_msl: float = 0.0
    latitude: float = 0.0
    longitude: float = 0.0
    rail_length: float = 2.4
    elevation_deg: float = 87.0     # rail angle above horizontal
    azimuth_deg: float = 0.0        # direction the rail points, clockwise from north

    @property
    def rail_direction(self) -> np.ndarray:
        el, az = math.radians(self.elevation_deg), math.radians(self.azimuth_deg)
        return np.array([math.cos(el) * math.sin(az), math.cos(el) * math.cos(az), math.sin(el)])

    def to_geodetic(self, east: float, north: float) -> tuple[float, float]:
        lat = self.latitude + math.degrees(north / EARTH_R)
        lon = self.longitude + math.degrees(east / (EARTH_R * math.cos(math.radians(self.latitude))))
        return lat, lon


@dataclass
class SimulationConfig:
    vehicle: Vehicle
    motor: MotorPerformance
    aero: AeroModel
    atmosphere: AtmosphereModel = field(default_factory=StandardAtmosphere)
    wind: WindModel = field(default_factory=ConstantWind)
    site: LaunchSite = field(default_factory=LaunchSite)
    recovery: RecoveryConfig | None = None
    dt: float = 0.005
    dt_descent: float = 0.05
    max_time: float = 600.0
    dry_mass_scale: float = 1.0      # Monte Carlo / what-if; != 1 marks results HYPOTHETICAL
    dry_cg_shift: float = 0.0        # m
    stop_at_apogee: bool = False
    propulsion: PropulsionSystem | None = None   # clusters / stages / airstarts; None: ``motor`` alone


@dataclass
class SimulationResult:
    t: np.ndarray
    position: np.ndarray            # (N,3) ENU m, z = AGL
    velocity: np.ndarray            # (N,3) m/s
    acceleration: np.ndarray        # (N,3) m/s^2 world (kinematic)
    specific_force_body: np.ndarray  # (N,3) m/s^2 - what an ideal accelerometer senses
    attitude: np.ndarray            # (N,4) quaternion body->ENU
    angular_velocity: np.ndarray    # (N,3) rad/s body
    mach: np.ndarray
    alpha: np.ndarray               # rad
    mass: np.ndarray
    cg: np.ndarray
    xcp: np.ndarray
    thrust: np.ndarray
    dynamic_pressure: np.ndarray
    pressure: np.ndarray            # Pa ambient (for virtual barometer)
    temperature: np.ndarray         # K ambient
    phase: list[str]
    events: list[tuple[float, str]]
    site: LaunchSite
    reference_diameter: float
    kind: DataKind = DataKind.SIMULATED
    notes: list[str] = field(default_factory=list)
    bodies: list[dict] = field(default_factory=list)   # separated stages flown to the ground

    @property
    def stability_margin(self) -> np.ndarray:
        return (self.xcp - self.cg) / self.reference_diameter

    def event_time(self, name: str) -> float | None:
        return next((t for t, n in self.events if n == name), None)

    def summary(self) -> dict[str, float | str | None]:
        z = self.position[:, 2]
        speed = np.linalg.norm(self.velocity, axis=1)
        i_ap = int(np.argmax(z))
        rail_t = self.event_time("rail_exit")
        rail_v = float(np.interp(rail_t, self.t, speed)) if rail_t is not None else None
        after_rail = self.t >= (rail_t or 0.0)
        powered = after_rail & (self.thrust > 0) & (self.mach > 0.05)
        # "burnout" as an accelerometer sees it: onset of axial deceleration (thrust < drag).
        # This is the definition flight data can measure; the motor's own end of thrust
        # is reported separately as motor_burnout_time_s.
        decel = np.nonzero((self.t > (rail_t or 0.0)) & (self.specific_force_body[:, 0] < 0))[0]
        bo = float(self.t[decel[0]]) if len(decel) else None
        return {
            "apogee_agl_m": float(z[i_ap]),
            "apogee_msl_m": float(z[i_ap] + self.site.altitude_msl),
            "time_to_apogee_s": float(self.t[i_ap]),
            "max_velocity_mps": float(speed.max()),
            "max_mach": float(self.mach.max()),
            "max_vertical_velocity_mps": float(self.velocity[:, 2].max()),
            "max_acceleration_mps2": float(np.linalg.norm(self.acceleration, axis=1).max()),
            # what an ideal axial accelerometer would read (comparable with flight data)
            "max_axial_accel_mps2": float(self.specific_force_body[:, 0].max()),
            "rail_exit_velocity_mps": rail_v,
            "burnout_time_s": bo,
            "motor_burnout_time_s": self.event_time("burnout"),
            "burnout_altitude_agl_m": float(np.interp(bo, self.t, z)) if bo else None,
            "burnout_velocity_mps": float(np.interp(bo, self.t, self.velocity[:, 2])) if bo else None,
            "min_stability_margin_cal": float(self.stability_margin[powered].min())
            if powered.any() else None,
            "max_dynamic_pressure_pa": float(self.dynamic_pressure.max()),
            "flight_time_s": float(self.t[-1]) if self.phase[-1] == "landed" else None,
            "landing_east_m": float(self.position[-1, 0]) if self.phase[-1] == "landed" else None,
            "landing_north_m": float(self.position[-1, 1]) if self.phase[-1] == "landed" else None,
            "kind": self.kind.value,
        }


class FlightSimulator:
    def __init__(self, config: SimulationConfig) -> None:
        self.cfg = config
        v = config.vehicle
        eng = MassPropertiesEngine(v)
        dry = eng.dry()
        self.m_dry = dry.mass * config.dry_mass_scale
        self.cg_dry = dry.cg + config.dry_cg_shift
        self.ixx_dry = dry.ixx * config.dry_mass_scale
        self.iyy_dry = dry.iyy * config.dry_mass_scale
        slot = v.motor_slot
        if slot is None:
            raise ValueError("vehicle has no motor slot")
        self.x_motor = slot.cg
        self.k_motor = slot.inertia_per_mass()
        self.S = config.aero.reference_area
        self.d = config.aero.reference_diameter
        self.aero = config.aero
        # several motors / stages: the general path (a single motor keeps the original, exact path)
        self.prop = config.propulsion
        if self.prop is not None and self.prop.is_simple:      # one motor: the plain path, with that motor
            from dataclasses import replace as _replace

            self.cfg = config = _replace(config, motor=self.prop.groups[0].motor, propulsion=None)
            self.prop = None
        self.simple = self.prop is None
        if not self.simple:
            self._setup_multi()

    # ---- several motors, stages ------------------------------------------------------------
    def _setup_multi(self) -> None:
        v = self.cfg.vehicle
        groups = self.prop.groups
        self.gx: list[float] = []
        for g in groups:
            if g.x is not None:
                self.gx.append(g.x)
                continue
            sl = v.motor_slot_for(g.stage)
            if sl is None:
                raise ValueError(f"stage {g.stage} has no motor slot for {g.motor.metadata.designation}")
            self.gx.append(sl.cg)
        self.attached: set[int] = set(v.stages) | {g.stage for g in groups}
        self.t_ign: list[float | None] = [0.0 if g.ignition == "launch" else None for g in groups]
        self.stage_burnout: dict[int, float] = {}
        self.separated: list[dict] = []
        self._dry_cache: dict[frozenset, tuple[float, float, float, float]] = {}
        self._aero_cache: dict[frozenset, AeroModel] = {frozenset(self.attached): self.cfg.aero}

    def _dry(self) -> tuple[float, float, float, float]:
        key = frozenset(self.attached)
        if key not in self._dry_cache:
            d = MassPropertiesEngine(self.cfg.vehicle.subset(key)).dry()
            k = self.cfg.dry_mass_scale
            self._dry_cache[key] = (d.mass * k, d.cg + self.cfg.dry_cg_shift, d.ixx * k, d.iyy * k)
        return self._dry_cache[key]

    def _aero_for(self, stages: frozenset) -> AeroModel:
        if stages not in self._aero_cache:
            from aerodyne.aero.analytical import AnalyticalAeroModel

            base: AeroModel = AnalyticalAeroModel(self.cfg.vehicle.subset(stages))
            a = self.cfg.aero
            if isinstance(a, ScaledAeroModel):
                base = ScaledAeroModel(base, a.cd_scale, a.cn_alpha_scale, a.xcp_shift)
            self._aero_cache[stages] = base
        return self._aero_cache[stages]

    def _motors(self, t: float) -> tuple[float, list[tuple[float, float]]]:
        """Total thrust and (mass, station) of every attached motor group at time t."""
        thrust, masses = 0.0, []
        for i, g in enumerate(self.prop.groups):
            if g.stage not in self.attached:
                continue
            ti = self.t_ign[i]
            if ti is None or t < ti:
                masses.append((g.count * g.motor.total_mass, self.gx[i]))
                continue
            tau = t - ti
            masses.append((g.count * g.motor.mass_at(tau), self.gx[i]))
            thrust += g.count * g.motor.thrust_at(tau)
        return thrust, masses

    def thrust(self, t: float) -> float:
        return self.cfg.motor.thrust_at(t) if self.simple else self._motors(t)[0]

    def _stage_events(self, t: float, r: np.ndarray, v: np.ndarray, events: list) -> None:
        """Airstarts, stage burnout and separation, ignition of the stage above (between steps)."""
        groups = self.prop.groups
        for i, g in enumerate(groups):
            if g.ignition == "time" and self.t_ign[i] is None and t >= g.delay and g.stage in self.attached:
                self.t_ign[i] = t
                events.append((t, f"ignition:{g.stage}"))
        for k in sorted(self.attached, reverse=True):
            sep = self.prop.separation(k)
            if sep is None:
                continue
            idx = [i for i, g in enumerate(groups) if g.stage == k]
            if any(self.t_ign[i] is None for i in idx):
                continue
            end = max(self.t_ign[i] + groups[i].motor.burn_time for i in idx)
            if t >= end and k not in self.stage_burnout:
                self.stage_burnout[k] = end
                events.append((end, f"burnout:{k}"))
            if k in self.stage_burnout and t >= self.stage_burnout[k] + sep.delay:
                spent = sum(groups[i].count * groups[i].motor.mass_at(t - self.t_ign[i]) for i in idx)
                sub = self.cfg.vehicle.subset({k})
                dry = MassPropertiesEngine(sub).dry()
                comps = [c for c in sub.components if c.length > 0 and "internal" not in c.tags]
                length = max((c.x + c.length for c in comps), default=0.3) - min((c.x for c in comps), default=0.0)
                diam = max((getattr(c, "outer_diameter", 0.0) or getattr(c, "diameter", 0.0) for c in comps), default=0.05)
                self.separated.append({"stage": k, "t": t, "r": r.copy(), "v": v.copy(), "mass": dry.mass + spent,
                                       "length": length, "diameter": diam, "parallel": sep.parallel,
                                       "count": max((c.instances for c in sub.components), default=1)})
                self.attached.discard(k)
                events.append((t, f"separation:{k}"))
                self.aero = self._aero_for(frozenset(self.attached))
                self.S, self.d = self.aero.reference_area, self.aero.reference_diameter
                for j, gg in enumerate(groups):
                    if gg.ignition == "separation" and gg.stage == k - 1 and self.t_ign[j] is None:
                        self.t_ign[j] = t + gg.delay
                        events.append((t + gg.delay, f"ignition:{gg.stage}"))

    def _burn_end(self) -> float | None:
        ends = [ti + g.motor.burn_time for ti, g in zip(self.t_ign, self.prop.groups) if ti is not None]
        return max(ends) if ends else None

    # ---- mass properties at time t (analytic, fast) ---------------------
    def mass_props(self, t: float) -> tuple[float, float, float, float]:
        if not self.simple:
            md, cgd, ixd, iyd = self._dry()
            _, motors = self._motors(t)
            m = md + sum(mi for mi, _ in motors)
            cg = (md * cgd + sum(mi * xi for mi, xi in motors)) / m
            ixx = ixd + sum(mi * self.k_motor[0] for mi, _ in motors)
            iyy = iyd + md * (cgd - cg) ** 2 + sum(mi * self.k_motor[1] + mi * (xi - cg) ** 2 for mi, xi in motors)
            return m, cg, ixx, iyy
        mm = self.cfg.motor.mass_at(t)
        m = self.m_dry + mm
        cg = (self.m_dry * self.cg_dry + mm * self.x_motor) / m
        ixx = self.ixx_dry + mm * self.k_motor[0]
        iyy = (self.iyy_dry + self.m_dry * (self.cg_dry - cg) ** 2
               + mm * self.k_motor[1] + mm * (self.x_motor - cg) ** 2)
        return m, cg, ixx, iyy

    def _env(self, z: float, t: float):
        atm = self.cfg.atmosphere.at(self.cfg.site.altitude_msl + z)
        wind = self.cfg.wind.at(max(z, 0.0), t)
        g = G0 * (EARTH_R / (EARTH_R + self.cfg.site.altitude_msl + z)) ** 2
        return atm, wind, g

    # ---- 6-DOF derivative ------------------------------------------------
    def _deriv(self, t: float, y: np.ndarray, out: dict | None = None) -> np.ndarray:
        r, v, q, w = y[0:3], y[3:6], quat.normalize(y[6:10]), y[10:13]
        m, cg, ixx, iyy = self.mass_props(t)
        atm, wind, g = self._env(r[2], t)
        R = quat.to_matrix(q)
        v_air_b = R.T @ (v - wind)
        V = float(np.linalg.norm(v_air_b))
        thrust = self.thrust(t)
        F_b = np.array([thrust, 0.0, 0.0])
        M_b = np.zeros(3)
        mach = alpha = 0.0
        xcp = cg
        qdyn = 0.5 * atm.density * V * V
        if V > 1e-3:
            mach = V / atm.speed_of_sound
            v_perp = np.array([0.0, v_air_b[1], v_air_b[2]])
            alpha = math.atan2(float(np.linalg.norm(v_perp)), float(v_air_b[0]))
            c = self.aero.coefficients(FlightCondition(
                mach=mach, reynolds_per_m=V / atm.kinematic_viscosity, alpha=alpha,
                altitude=r[2] + self.cfg.site.altitude_msl, thrusting=thrust > 0))
            xcp = c.xcp
            F_axial = -qdyn * self.S * c.ca * math.copysign(1.0, v_air_b[0])
            F_normal = -qdyn * self.S * c.cn_alpha * v_perp / V   # CN ~ CNa sin(alpha)
            F_b = F_b + np.array([F_axial, 0.0, 0.0]) + F_normal
            M_b += np.cross(np.array([cg - xcp, 0.0, 0.0]), F_normal)
            if c.fin_station is not None and c.fin_cn_alpha > 0:
                lever = c.fin_station - cg
                c_damp = 0.5 * atm.density * V * self.S * c.fin_cn_alpha * lever * lever
                M_b += -c_damp * np.array([0.0, w[1], w[2]])
        a_w = R @ F_b / m + np.array([0.0, 0.0, -g])
        I = np.array([ixx, iyy, iyy])
        w_dot = (M_b - np.cross(w, I * w)) / I
        q_dot = 0.5 * quat.multiply(q, np.array([0.0, *w]))
        if out is not None:
            out.update(m=m, cg=cg, xcp=xcp, mach=mach, alpha=alpha, thrust=thrust, qdyn=qdyn,
                       sf=F_b / m, acc=a_w, p=atm.pressure, T=atm.temperature)
        return np.concatenate([v, a_w, q_dot, w_dot])

    def _rk4(self, t: float, y: np.ndarray, h: float) -> np.ndarray:
        k1 = self._deriv(t, y)
        k2 = self._deriv(t + h / 2, y + h / 2 * k1)
        k3 = self._deriv(t + h / 2, y + h / 2 * k2)
        k4 = self._deriv(t + h, y + h * k3)
        yn = y + h / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
        yn[6:10] = quat.normalize(yn[6:10])
        return yn

    # ---- main loop ---------------------------------------------------------
    def run(self) -> SimulationResult:
        cfg = self.cfg
        u = cfg.site.rail_direction
        q0 = quat.from_two_vectors(np.array([1.0, 0.0, 0.0]), u)
        rec: dict[str, list] = {k: [] for k in (
            "t", "r", "v", "a", "sf", "q", "w", "mach", "alpha", "m", "cg", "xcp", "thrust",
            "qdyn", "p", "T", "phase")}
        events: list[tuple[float, str]] = []

        def log(t, y, info, phase):
            rec["t"].append(t); rec["r"].append(y[0:3].copy()); rec["v"].append(y[3:6].copy())
            rec["q"].append(y[6:10].copy()); rec["w"].append(y[10:13].copy())
            rec["a"].append(info["acc"]); rec["sf"].append(info["sf"])
            for k, src in (("mach", "mach"), ("alpha", "alpha"), ("m", "m"), ("cg", "cg"),
                           ("xcp", "xcp"), ("thrust", "thrust"), ("qdyn", "qdyn"),
                           ("p", "p"), ("T", "T")):
                rec[k].append(info[src])
            rec["phase"].append(phase)

        # --- launch rail (1-DOF along rail) ---
        t, s, sdot = 0.0, 0.0, 0.0
        h = cfg.dt
        m0 = self.mass_props(0.0)[0]
        peak = cfg.motor.peak_thrust if self.simple else sum(
            g.count * g.motor.peak_thrust for g in self.prop.groups if g.ignition == "launch")
        if peak <= m0 * G0 * u[2]:
            raise ValueError("motor peak thrust does not exceed vehicle weight - no liftoff")
        liftoff = False
        while s < cfg.site.rail_length:
            m, cg, _, _ = self.mass_props(t)
            atm, _, g = self._env(s * u[2], t)
            if not self.simple:
                self._stage_events(t, s * u, sdot * u, events)
            T = self.thrust(t)
            qd = 0.5 * atm.density * sdot * sdot
            ca = 0.0
            mach = sdot / atm.speed_of_sound
            if sdot > 0.1:
                ca = self.aero.coefficients(FlightCondition(
                    mach=mach, reynolds_per_m=sdot / atm.kinematic_viscosity, thrusting=T > 0)).ca
            acc = (T - qd * self.S * ca) / m - g * u[2]
            if not liftoff and acc <= 0:
                acc = 0.0
            elif not liftoff:
                liftoff = True
                events.append((t, "liftoff"))
            y = np.concatenate([s * u, sdot * u, q0, np.zeros(3)])
            info = dict(acc=acc * u, sf=np.array([acc + g * u[2], 0.0, 0.0]), m=m, cg=cg,
                        xcp=self.aero.coefficients(FlightCondition(
                            mach=max(mach, 0.01), reynolds_per_m=1e6)).xcp,
                        mach=mach, alpha=0.0, thrust=T, qdyn=qd, p=atm.pressure,
                        T=atm.temperature)
            log(t, y, info, "rail")
            sdot += acc * h
            s += sdot * h
            t += h
            if t > 30:
                raise RuntimeError("vehicle failed to leave the rail within 30 s")
        events.append((t, "rail_exit"))
        y = np.concatenate([s * u, sdot * u, q0, np.zeros(3)])

        # --- free flight (6-DOF) until apogee ---
        burnout_logged = False
        phase = "powered"
        while t < cfg.max_time:
            info: dict = {}
            if not self.simple:
                self._stage_events(t, y[0:3], y[3:6], events)
            self._deriv(t, y, info)
            if self.simple:
                if not burnout_logged and t >= cfg.motor.burn_time:
                    events.append((t, "burnout"))
                    burnout_logged = True
                    phase = "coast"
            else:
                phase = "powered" if info["thrust"] > 0 else "coast"
            log(t, y, info, phase)
            yn = self._rk4(t, y, h)
            if y[5] > 0 and yn[5] <= 0:
                frac = y[5] / (y[5] - yn[5])
                events.append((float(t + frac * h), "apogee"))
                t += h
                y = yn
                break
            t += h
            y = yn
            if y[2] < 0:
                events.append((t, "ground_impact"))
                break

        notes = []
        if not self.simple:
            end = self._burn_end()
            if end is not None and end <= t:
                events.append((end, "burnout"))
            notes.append("several motors / stages: " + "; ".join(self.prop.describe()))
        if cfg.dry_mass_scale != 1.0 or cfg.dry_cg_shift != 0.0:
            notes.append("dry mass/CG perturbed from design values")

        # --- descent ---
        t_apogee = t
        if not cfg.stop_at_apogee and y[2] > 0:
            if cfg.recovery is None:
                notes.append("no recovery configuration: ballistic 6-DOF descent")
                while y[2] > 0 and t < cfg.max_time:
                    info = {}
                    self._deriv(t, y, info)
                    log(t, y, info, "ballistic")
                    y = self._rk4(t, y, h)
                    t += h
            else:
                t, y = self._descent(t, y, t_apogee, events, log)
            if y[2] <= 0:
                events.append((t, "landing"))
                info = {}
                self._deriv(t, y, info)
                log(t, y, info, "landed")

        events.sort(key=lambda e: e[0])
        bodies = [] if self.simple else [self._fly_body(b) for b in self.separated]
        res = SimulationResult(
            t=np.array(rec["t"]), position=np.array(rec["r"]), velocity=np.array(rec["v"]),
            acceleration=np.array(rec["a"]), specific_force_body=np.array(rec["sf"]),
            attitude=np.array(rec["q"]), angular_velocity=np.array(rec["w"]),
            mach=np.array(rec["mach"]), alpha=np.array(rec["alpha"]), mass=np.array(rec["m"]),
            cg=np.array(rec["cg"]), xcp=np.array(rec["xcp"]), thrust=np.array(rec["thrust"]),
            dynamic_pressure=np.array(rec["qdyn"]), pressure=np.array(rec["p"]),
            temperature=np.array(rec["T"]), phase=rec["phase"], events=events, site=cfg.site,
            reference_diameter=cfg.aero.reference_diameter, notes=notes,
            kind=DataKind.SIMULATED, bodies=bodies)
        return res

    def _fly_body(self, b: dict) -> dict:
        """A separated stage flown to the ground as a tumbling point mass (drag ESTIMATED)."""
        r, v, t = b["r"].copy(), b["v"].copy(), b["t"]
        m = b["mass"]
        cda = 0.6 * b["length"] * b["diameter"] * max(1, b["count"]) + 0.5 * math.pi * b["diameter"] ** 2 / 4
        h = 0.02
        traj = [(t, *r)]
        apo = r[2]
        while r[2] > 0 and t < b["t"] + self.cfg.max_time:
            atm, wind, g = self._env(r[2], t)
            va = v - wind
            a = -0.5 * atm.density * float(np.linalg.norm(va)) * va * cda / m + np.array([0.0, 0.0, -g])
            v = v + a * h
            r = r + v * h
            t += h
            apo = max(apo, r[2])
            if len(traj) < 4000 and int(round(t / h)) % 10 == 0:
                traj.append((t, *r))
        traj.append((t, *r))
        return {"stage": b["stage"], "parallel": b["parallel"], "separation_time_s": b["t"], "separation_altitude_m": float(b["r"][2]),
                "separation_speed_mps": float(np.linalg.norm(b["v"])), "mass_kg": m, "apogee_agl_m": float(apo),
                "landing_time_s": float(t), "landing_east_m": float(r[0]), "landing_north_m": float(r[1]),
                "impact_speed_mps": float(np.linalg.norm(v)), "trajectory": [list(map(float, p)) for p in traj],
                "recovery": "tumble (no recovery device modelled)", "kind": "SIMULATED, drag ESTIMATED"}

    def _descent(self, t, y, t_apogee, events, log):
        cfg = self.cfg
        rc = cfg.recovery
        m, cg, _, _ = self.mass_props(t)
        r, v, q = y[0:3].copy(), y[3:6].copy(), y[6:10].copy()
        deploy: dict[str, float] = {}
        h = cfg.dt_descent
        R = quat.to_matrix(q)
        while r[2] > 0 and t < cfg.max_time:
            for d in rc.devices:
                if d.name in deploy:
                    continue
                if d.deploy_event == "apogee" and t >= t_apogee + d.delay:
                    deploy[d.name] = t
                    events.append((t, f"deploy:{d.name}"))
                elif (d.deploy_event == "altitude" and d.deploy_altitude_agl is not None
                      and r[2] <= d.deploy_altitude_agl and deploy.get(f"_{d.name}") is None):
                    deploy[f"_{d.name}"] = t
                if f"_{d.name}" in deploy and d.name not in deploy and t >= deploy[f"_{d.name}"] + d.delay:
                    deploy[d.name] = t
                    events.append((t, f"deploy:{d.name}"))
            atm, wind, g = self._env(r[2], t)
            v_air = v - wind
            V = float(np.linalg.norm(v_air))
            cda = rc.deployed_cd_area({k: v_ for k, v_ in deploy.items() if not k.startswith("_")}, t)
            if not any(not k.startswith("_") for k in deploy):
                # before first deployment: vehicle falls with its own axial drag, approx.
                cda = max(cda, 0.5 * self.S)
            F = -0.5 * atm.density * V * v_air * cda
            a = F / m + np.array([0.0, 0.0, -g])
            info = dict(acc=a, sf=R.T @ (F / m), m=m, cg=cg, xcp=cg, mach=V / atm.speed_of_sound,
                        alpha=0.0, thrust=0.0, qdyn=0.5 * atm.density * V * V, p=atm.pressure,
                        T=atm.temperature)
            y = np.concatenate([r, v, q, np.zeros(3)])
            log(t, y, info, "descent")
            v = v + a * h
            r = r + v * h
            t += h
        y = np.concatenate([r, v, q, np.zeros(3)])
        return t, y
