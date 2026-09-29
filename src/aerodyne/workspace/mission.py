"""Mission: everything needed to predict one flight - which vehicle revision,
which motor, where, in what wind and atmosphere, and the limits it must meet."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from aerodyne.dynamics.simulator import LaunchSite
from aerodyne.environment.atmosphere import StandardAtmosphere
from aerodyne.environment.wind import ConstantWind, LayeredWind, PowerLawWind
from aerodyne.montecarlo.engine import Normal, Uniform, UncertaintyModel


@dataclass
class Limits:
    """Readiness thresholds. Defaults are common engineering rules of thumb -
    set them to your safety code, range and RSO requirements."""

    min_margin_cal: float = 1.0
    max_margin_cal: float = 4.0            # above this: warn (over-stable, weathercocks)
    min_rail_exit_mps: float = 15.0
    min_thrust_to_weight: float = 5.0
    altitude_ceiling_agl_m: float | None = None   # waiver / range ceiling
    field_radius_m: float | None = None           # recovery area radius around the pad
    max_drogue_rate_mps: float = 30.0
    main_rate_min_mps: float = 3.5
    main_rate_max_mps: float = 7.6
    max_mach_analytical: float = 0.8       # above: analytical aero is weak - use table data


@dataclass
class Mission:
    id: str
    name: str
    vehicle_id: str
    revision: str
    motor_key: str
    site: dict[str, float] = field(default_factory=lambda: dataclasses.asdict(LaunchSite()))
    wind: dict[str, Any] = field(default_factory=lambda: {"model": "power_law", "speed": 3.0,
                                                          "from_deg": 270.0})
    atmosphere: dict[str, float] = field(default_factory=lambda: {
        "temperature_offset": 0.0, "sea_level_pressure": 101325.0, "relative_humidity": 0.0})
    limits: Limits = field(default_factory=Limits)
    uncertainty: dict[str, float] = field(default_factory=lambda: {
        "dry_mass_rel_sigma": 0.02, "cg_sigma_m": 0.01, "impulse_rel_sigma": 0.03,
        "burn_time_rel_sigma": 0.03, "cd_rel_sigma": 0.08, "wind_speed_sigma": 1.5,
        "wind_dir_sigma_deg": 30.0,
        "gust_sigma_max": 1.5, "temperature_sigma_k": 5.0, "elevation_sigma_deg": 1.0,
        "azimuth_sigma_deg": 2.0})

    site_id: str | None = None            # launch-site library entry the site/limits came from
    # several motors (clusters, stages, airstarts): [{motor_key, count, stage, ignition, delay}];
    # empty = motor_key alone. separations: [{stage, delay, parallel}]
    motors: list[dict[str, Any]] = field(default_factory=list)
    separations: list[dict[str, Any]] = field(default_factory=list)

    # ---- serialization ------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        for k in ("site_id", "motors", "separations"):    # optional: older missions keep their hash
            if not d.get(k):
                d.pop(k, None)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Mission":
        d = dict(d)
        d["limits"] = Limits(**(d.get("limits") or {}))
        return cls(**d)

    # ---- model objects ----------------------------------------------------------------
    def launch_site(self) -> LaunchSite:
        return LaunchSite(**self.site)

    def atmosphere_model(self) -> StandardAtmosphere:
        a = self.atmosphere
        return StandardAtmosphere(a.get("temperature_offset", 0.0),
                                  a.get("sea_level_pressure", 101325.0),
                                  a.get("relative_humidity", 0.0))

    def wind_model(self):
        w = self.wind
        m = w.get("model", "constant")
        if m == "power_law":
            return PowerLawWind(w.get("speed", 0.0), w.get("from_deg", 0.0),
                                w.get("reference_height", 10.0), w.get("exponent", 1 / 7))
        if m == "layered":
            return LayeredWind(w["altitudes"], w["speeds"], w["from_deg"])
        return ConstantWind(w.get("speed", 0.0), w.get("from_deg", 0.0))

    def uncertainty_model(self) -> UncertaintyModel:
        u = self.uncertainty
        s = self.site
        factory = None
        if self.wind.get("model") == "layered":
            w = self.wind

            def layered(scale: float, offset: float) -> LayeredWind:
                return LayeredWind(w["altitudes"], [v * scale for v in w["speeds"]],
                                   [d + offset for d in w["from_deg"]])
            factory = layered
        return UncertaintyModel(
            wind_factory=factory,
            wind_scale=Normal(1.0, u.get("wind_speed_rel_sigma", 0.25)),
            wind_dir_offset=Normal(0.0, u["wind_dir_sigma_deg"]),
            dry_mass_scale=Normal(1.0, u["dry_mass_rel_sigma"]),
            dry_cg_shift=Normal(0.0, u["cg_sigma_m"]),
            impulse_scale=Normal(1.0, u["impulse_rel_sigma"]),
            burn_time_scale=Normal(1.0, u["burn_time_rel_sigma"]),
            cd_scale=Normal(1.0, u["cd_rel_sigma"]),
            # dispersions around the mission's forecast wind (surface reference speed)
            # surface-wind dispersion (used when the wind is not a layered profile)
            wind_speed=Normal(float(self.wind.get("speed", 0.0) or 0.0) if factory is None else 0.0,
                              u["wind_speed_sigma"]),
            wind_from_deg=Normal(float(self.wind.get("from_deg", 0.0)) if factory is None else 0.0,
                                 u["wind_dir_sigma_deg"]),
            gust_sigma=Uniform(0.0, u["gust_sigma_max"]),
            temperature_offset=Normal(self.atmosphere.get("temperature_offset", 0.0),
                                      u["temperature_sigma_k"]),
            sea_level_pressure=Normal(self.atmosphere.get("sea_level_pressure", 101325.0), 300.0),
            elevation_deg=Normal(s.get("elevation_deg", 87.0), u["elevation_sigma_deg"]),
            azimuth_deg=Normal(s.get("azimuth_deg", 0.0), u["azimuth_sigma_deg"]),
        )
