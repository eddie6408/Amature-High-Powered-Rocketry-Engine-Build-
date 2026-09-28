"""Reference example vehicle used by the CLI, docs and tests.

AERODYNE-EX1 is a notional 66 mm dual-deploy rocket flying a HYPOTHETICAL
synthetic H-class motor. It is an illustration, not a flight-ready design.
"""

from __future__ import annotations

from aerodyne.aero.analytical import AnalyticalAeroModel
from aerodyne.dynamics.simulator import LaunchSite, SimulationConfig
from aerodyne.environment.atmosphere import StandardAtmosphere
from aerodyne.environment.wind import PowerLawWind
from aerodyne.propulsion.motor import MotorPerformance, synthetic_motor
from aerodyne.recovery.recovery import RecoveryConfig, RecoveryDevice
from aerodyne.vehicle.components import (
    BodyTube,
    Bulkhead,
    FinSet,
    MotorSlot,
    NoseCone,
    PointMass,
)
from aerodyne.vehicle.vehicle import Vehicle


def example_vehicle() -> Vehicle:
    d = 0.066
    v = Vehicle(name="AERODYNE-EX1", launch_lug_drag_area=2e-4)
    v.add(NoseCone("nose cone", x=0.0, length_=0.30, diameter=d, shape="ogive",
                   wall_thickness=0.0015, material="fiberglass"))
    v.add(PointMass("nose ballast/tracker", x=0.20, mass_estimate=0.06, length_=0.05,
                    radius=0.02, tags={"payload"}))
    v.add(BodyTube("upper airframe", x=0.30, length_=0.40, outer_diameter=d,
                   wall_thickness=0.0015, material="fiberglass"))
    v.add(PointMass("main parachute", x=0.35, mass_estimate=0.12, length_=0.15, radius=0.025,
                    tags={"recovery"}))
    v.add(PointMass("avionics bay", x=0.66, mass_estimate=0.22, length_=0.16, radius=0.03,
                    tags={"avionics"}))
    v.add(BodyTube("lower airframe", x=0.70, length_=0.55, outer_diameter=d,
                   wall_thickness=0.0015, material="fiberglass"))
    v.add(PointMass("drogue + shock cord", x=0.75, mass_estimate=0.08, length_=0.1,
                    radius=0.025, tags={"recovery"}))
    v.add(Bulkhead("motor mount bulkhead", x=0.99, diameter=d - 0.003, thickness=0.006))
    v.add(BodyTube("motor mount tube", x=1.00, length_=0.25, outer_diameter=0.041,
                   wall_thickness=0.001, material="cardboard"))
    v.add(FinSet("fins", x=1.08, count=3, root_chord=0.14, tip_chord=0.06, span=0.075,
                 sweep=0.07, thickness=0.0024, body_radius=d / 2, material="g10"))
    v.add(MotorSlot("motor", x=1.00, motor_length=0.25, motor_diameter=0.038))
    return v


def example_motor() -> MotorPerformance:
    return synthetic_motor()


def example_recovery() -> RecoveryConfig:
    return RecoveryConfig(devices=(
        RecoveryDevice("drogue", cd=1.5, diameter=0.30, deploy_event="apogee", delay=1.0),
        RecoveryDevice("main", cd=2.2, diameter=0.91, deploy_event="altitude",
                       deploy_altitude_agl=150.0),
    ), body_cd_area=0.004)


def example_config(**overrides) -> SimulationConfig:
    v = example_vehicle()
    kw = dict(vehicle=v, motor=example_motor(), aero=AnalyticalAeroModel(v),
              atmosphere=StandardAtmosphere(), wind=PowerLawWind(4.0, 270.0),
              site=LaunchSite(altitude_msl=100.0, latitude=35.0, longitude=-117.0,
                              rail_length=1.8, elevation_deg=87.0, azimuth_deg=270.0),
              recovery=example_recovery())
    kw.update(overrides)
    return SimulationConfig(**kw)
