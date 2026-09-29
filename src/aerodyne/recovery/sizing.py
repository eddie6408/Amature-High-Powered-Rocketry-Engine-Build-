"""Recovery sizing: canopy size for a landing rate, standard sizes, landing energy per section,
and a drift table over wind speed and main-deploy altitude.

Descent physics: steady descent where weight = drag, v = sqrt(2 m g / (rho Cd A)). Canopy Cd
values here are typical published figures (ESTIMATED); use your canopy maker's Cd.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from aerodyne.environment.atmosphere import G0, StandardAtmosphere
from aerodyne.environment.wind import PowerLawWind
from aerodyne.recovery.recovery import RecoveryConfig, analyze_recovery

IN = 0.0254
FT_LBF = 1.3558179
# nominal canopy diameters commonly sold for high-power rockets (inches)
STANDARD_SIZES_IN = (12, 15, 18, 24, 30, 36, 42, 48, 54, 60, 72, 84, 96, 108, 120, 144, 168)
CANOPY_CD = {  # typical drag coefficient on nominal area (ESTIMATED)
    "round / hemispherical": 1.5, "elliptical": 1.5, "toroidal": 2.2, "cruciform (X-form)": 0.8, "flat sheet": 0.75,
}


def density_at(altitude_msl: float, temperature_c: float | None = None) -> float:
    std = StandardAtmosphere()
    off = 0.0 if temperature_c is None else temperature_c + 273.15 - std.at(altitude_msl).temperature
    return StandardAtmosphere(off).at(altitude_msl).density


def rate_for(mass: float, diameter: float, cd: float, density: float, extra_cd_area: float = 0.0) -> float:
    cda = cd * math.pi * diameter ** 2 / 4 + extra_cd_area
    if cda <= 0:
        raise ValueError("drag area must be positive")
    return math.sqrt(2 * mass * G0 / (density * cda))


def diameter_for(mass: float, rate: float, cd: float, density: float, extra_cd_area: float = 0.0) -> float:
    if mass <= 0 or rate <= 0 or cd <= 0 or density <= 0:
        raise ValueError("mass, rate, Cd and density must be positive")
    need = 2 * mass * G0 / (density * rate * rate) - extra_cd_area
    return math.sqrt(4 * max(need, 0.0) / (math.pi * cd))


def size_canopy(mass_kg: float, target_rate: float, cd: float, altitude_msl: float = 0.0, temperature_c: float | None = None,
                sections_kg: list[float] | None = None, extra_cd_area: float = 0.0) -> dict[str, Any]:
    rho = density_at(altitude_msl, temperature_c)
    d = diameter_for(mass_kg, target_rate, cd, rho, extra_cd_area)
    sections = [s for s in (sections_kg or []) if s > 0] or [mass_kg]

    def option(din: float) -> dict[str, Any]:
        v = rate_for(mass_kg, din * IN, cd, rho, extra_cd_area)
        ke = [0.5 * m * v * v for m in sections]
        return {"diameter_in": din, "diameter_m": din * IN, "rate_mps": v, "rate_fps": v / 0.3048,
                "section_energy_j": ke, "section_energy_ftlbf": [e / FT_LBF for e in ke],
                "max_section_energy_ftlbf": max(ke) / FT_LBF}

    bigger = [s for s in STANDARD_SIZES_IN if s * IN >= d]
    picks = ([max([s for s in STANDARD_SIZES_IN if s * IN < d], default=None)] if d > STANDARD_SIZES_IN[0] * IN else []) + bigger[:2]
    rec = bigger[0] if bigger else None
    return {"density": rho, "required_diameter_m": d, "required_diameter_in": d / IN,
            "options": [{**option(s), "recommended": s == rec} for s in picks if s is not None],
            "sections_kg": sections,
            "notes": ["Cd is the canopy maker's figure on nominal area; typical values are ESTIMATED.",
                      "A common landing-energy guideline is 75 ft·lbf or less per tethered section."]}


def drift_table(mass: float, config: RecoveryConfig, apogee_agl: float, site_altitude: float,
                atmosphere=None, winds: list[float] | None = None, main_altitudes: list[float] | None = None,
                wind_from_deg: float = 270.0) -> dict[str, Any]:
    """Drift, descent time and landing rate from apogee over wind speed x main-deploy altitude."""
    atmosphere = atmosphere or StandardAtmosphere()
    winds = winds if winds is not None else [0.0, 2.5, 5.0, 7.5, 8.9]
    mains = [d for d in config.devices if d.deploy_event == "altitude"]
    alts = main_altitudes if (main_altitudes is not None and mains) else ([mains[0].deploy_altitude_agl] if mains else [None])
    rows = []
    for alt in alts:
        cfg = config
        if alt is not None:
            cfg = RecoveryConfig(devices=tuple(replace(d, deploy_altitude_agl=alt) if d is mains[0] else d for d in config.devices),
                                 body_cd_area=config.body_cd_area)
        cells = []
        for w in winds:
            est = analyze_recovery(mass, cfg, apogee_agl, site_altitude, atmosphere, PowerLawWind(w, wind_from_deg), dt=0.05)
            land = est.timeline[-1][0]
            cells.append({"wind_mps": w, "drift_m": est.drift_distance, "descent_s": land,
                          "rates": est.descent_rates})
        rows.append({"main_altitude_m": alt, "cells": cells})
    return {"apogee_agl_m": apogee_agl, "winds_mps": winds, "rows": rows, "has_main": bool(mains),
            "note": "Drift from apogee over a flat field with a power-law wind profile; add the powered-flight drift "
                    "and weathercocking from the full simulation for the landing point."}
