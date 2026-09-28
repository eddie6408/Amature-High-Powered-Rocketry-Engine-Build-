"""Digital twin.

Holds geometry, mass properties, motor, aerodynamic model, atmosphere,
avionics and recovery configuration for one vehicle revision; is consumed by
simulation, mission planning, flight analysis, telemetry analysis and
post-flight reconstruction; and records every post-flight validation.
Updates proposed from flight data are *proposals* requiring engineering
review - the twin is never silently re-tuned.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from aerodyne.aero.analytical import AnalyticalAeroModel, barrowman_cp
from aerodyne.aero.model import AeroModel, ScaledAeroModel
from aerodyne.analysis.comparison import compare
from aerodyne.core.provenance import DataKind, Tagged, stable_hash
from aerodyne.dynamics.simulator import FlightSimulator, LaunchSite, SimulationConfig
from aerodyne.environment.atmosphere import AtmosphereModel, StandardAtmosphere
from aerodyne.environment.wind import ConstantWind, WindModel
from aerodyne.propulsion.motor import MotorPerformance
from aerodyne.recovery.recovery import RecoveryConfig
from aerodyne.vehicle.mass import MassPropertiesEngine, MassProperties
from aerodyne.vehicle.vehicle import Vehicle


@dataclass(frozen=True)
class AvionicsConfig:
    hardware_version: str = "FC-HW-001"
    firmware_version: str = "FW-0.1.0"
    firmware_hash: str = ""
    sensor_config: str = "SENSOR-CONFIG-001"
    telemetry_protocol: str = "TELEMETRY-2"
    sensors: tuple[str, ...] = ("imu_accel", "imu_gyro", "baro", "gnss", "battery", "temperature")


@dataclass
class TwinValidation:
    flight_id: str
    timestamp: str
    metrics: dict[str, dict[str, float | None]]
    tolerances: dict[str, float]
    status: str                     # VALIDATED | DEVIATION
    actual_kind: str


@dataclass
class DigitalTwin:
    vehicle_id: str
    revision: str
    vehicle: Vehicle
    motor: MotorPerformance
    recovery: RecoveryConfig | None = None
    avionics: AvionicsConfig = field(default_factory=AvionicsConfig)
    aero: AeroModel | None = None
    atmosphere: AtmosphereModel = field(default_factory=StandardAtmosphere)
    validations: list[TwinValidation] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.aero is None:
            self.aero = AnalyticalAeroModel(self.vehicle)

    # ---- engineering state ----------------------------------------------------
    def mass_properties(self, configuration: str = "FULL") -> MassProperties:
        return MassPropertiesEngine(self.vehicle).configuration(configuration, self.motor)

    def stability(self, mach: float = 0.3) -> dict[str, float]:
        cna, xcp, _ = barrowman_cp(self.vehicle, mach)
        out = {"cn_alpha": cna, "xcp_m": xcp, "reference_diameter_m": self.vehicle.reference_diameter}
        for cfg in ("FULL", "MOTOR_SPENT"):
            mp = self.mass_properties(cfg)
            out[f"cg_{cfg.lower()}_m"] = mp.cg
            out[f"margin_{cfg.lower()}_cal"] = (xcp - mp.cg) / self.vehicle.reference_diameter
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "vehicle_id": self.vehicle_id, "revision": self.revision,
            "vehicle": self.vehicle.to_dict(),
            "motor": {**self.motor.summary(), "data_hash": self.motor.data_hash},
            "recovery": self.recovery, "avionics": self.avionics,
            "aero_model": type(self.aero).__name__,
            "atmosphere_model": type(self.atmosphere).__name__,
        }

    @property
    def config_hash(self) -> str:
        return stable_hash(self.to_dict())

    # ---- consumers -------------------------------------------------------------
    def simulation_config(self, site: LaunchSite | None = None, wind: WindModel | None = None,
                          **kw: Any) -> SimulationConfig:
        return SimulationConfig(vehicle=self.vehicle, motor=self.motor, aero=self.aero,
                                atmosphere=self.atmosphere, wind=wind or ConstantWind(),
                                site=site or LaunchSite(), recovery=self.recovery, **kw)

    # ---- post-flight -------------------------------------------------------------
    def validate_against_flight(self, flight_id: str, predicted: dict, actual: dict,
                                tolerances: dict[str, float] | None = None) -> TwinValidation:
        tol = tolerances or {"apogee_agl_m": 0.05, "max_vertical_velocity_mps": 0.05,
                             "time_to_apogee_s": 0.05, "burnout_velocity_mps": 0.05}
        rows = {r.key: r for r in compare(predicted, actual)}
        metrics = {}
        ok = True
        for k, lim in tol.items():
            r = rows.get(k)
            pct = None if r is None or r.pct_error is None else r.pct_error / 100.0
            metrics[k] = {"predicted": r.simulated if r else None, "actual": r.actual if r else None,
                          "rel_error": pct, "tolerance": lim}
            if pct is None or abs(pct) > lim:
                ok = False
        v = TwinValidation(flight_id, datetime.now(timezone.utc).isoformat(), metrics, tol,
                           "VALIDATED" if ok else "DEVIATION", str(actual.get("kind", "MEASURED")))
        self.validations.append(v)
        return v

    def propose_drag_calibration(self, measured_apogee_agl: float, base: SimulationConfig,
                                 lo: float = 0.6, hi: float = 1.6, iters: int = 18
                                 ) -> Tagged[float]:
        """Find the Cd scale factor that reproduces a measured apogee (bisection).
        Returned as an ESTIMATED proposal: apply only after engineering review,
        because other contributors (mass, motor, wind) can mimic a drag error."""
        def apogee(scale: float) -> float:
            cfg = replace(base, aero=ScaledAeroModel(base.aero, cd_scale=scale), stop_at_apogee=True,
                          dt=max(base.dt, 0.01))
            return FlightSimulator(cfg).run().summary()["apogee_agl_m"]

        a_lo, a_hi = apogee(lo), apogee(hi)
        if not (a_hi <= measured_apogee_agl <= a_lo):
            raise ValueError("measured apogee is outside the range reachable by Cd scaling "
                             f"[{a_hi:.0f}, {a_lo:.0f}] m; drag alone cannot explain it")
        for _ in range(iters):
            mid = 0.5 * (lo + hi)
            if apogee(mid) > measured_apogee_agl:
                lo = mid
            else:
                hi = mid
        return Tagged(0.5 * (lo + hi), DataKind.ESTIMATED, "",
                      note="proposed Cd scale; requires engineering review before adoption")
