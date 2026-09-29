"""Fin flutter: the airspeed at which a fin's torsional stiffness can no longer resist
aerodynamic twisting, from NACA TN 4197 (Martin, 1958) in the form widely used for
amateur rockets:

    Vf = a * sqrt( G / ( 1.337 * AR^3 * P * (λ + 1) / (2 * (AR + 2) * (t/c)^3) ) )

a: speed of sound, G: in-plane shear modulus of the fin material, P: air pressure, AR: aspect
ratio (span^2 / fin area), λ: taper ratio (tip / root chord), t/c: thickness / root chord.
It assumes a flat, solid, uniform fin clamped at the root; it is an ESTIMATE whose value
depends strongly on G (enter your laminate's value) and on t/c (cubed). Keep a margin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from aerodyne.vehicle.components import FinSet, _material


@dataclass(frozen=True)
class FlutterInputs:
    shear_modulus: float      # Pa
    source: str               # where G came from


def fin_shear_modulus(fin: FinSet) -> FlutterInputs | None:
    if fin.shear_modulus_gpa:
        return FlutterInputs(fin.shear_modulus_gpa * 1e9, "declared on the fin set")
    m = _material(fin.material)
    if m.shear_modulus:
        return FlutterInputs(m.shear_modulus, f"typical value for {m.name} (ESTIMATED)")
    return None


def flutter_velocity(fin: FinSet, shear_modulus: float, pressure: float, sound_speed: float) -> float:
    area = fin.planform_area
    if area <= 0 or fin.root_chord <= 0 or fin.thickness <= 0:
        raise ValueError("fin needs positive area, root chord and thickness")
    ar = fin.span ** 2 / area
    lam = fin.tip_chord / fin.root_chord
    tc = fin.thickness / fin.root_chord
    denom = 1.337 * ar ** 3 * pressure * (lam + 1.0) / (2.0 * (ar + 2.0) * tc ** 3)
    return sound_speed * math.sqrt(shear_modulus / denom)


def flutter_along_trajectory(fin: FinSet, result: Any, atmosphere: Any) -> dict[str, Any] | None:
    """Flutter speed vs airspeed along a simulated flight (powered and coast, until apogee).

    Returns the minimum ratio Vf / V and where it occurs, or None when no shear modulus is known."""
    g = fin_shear_modulus(fin)
    if g is None:
        return None
    t = np.asarray(result.t)
    site_alt = float(getattr(getattr(result, "site", None), "altitude_msl", 0.0) or 0.0)
    alt_msl = np.asarray(result.position)[:, 2] + site_alt
    speed = np.linalg.norm(np.asarray(result.velocity), axis=1)
    t_apo = result.event_time("apogee") or t[-1]
    sel = (t <= t_apo) & (speed > 1.0)
    if not np.any(sel):
        return None
    vf = np.array([flutter_velocity(fin, g.shear_modulus, atmosphere.at(float(z)).pressure,
                                    atmosphere.at(float(z)).speed_of_sound) for z in alt_msl[sel]])
    ratio = vf / speed[sel]
    i = int(np.argmin(ratio))
    return {"min_ratio": float(ratio[i]), "time_s": float(t[sel][i]), "speed_mps": float(speed[sel][i]),
            "flutter_speed_mps": float(vf[i]), "altitude_agl_m": float(np.asarray(result.position)[:, 2][sel][i]),
            "flutter_speed_sea_level_mps": flutter_velocity(fin, g.shear_modulus, 101325.0, 340.3),
            "shear_modulus_gpa": g.shear_modulus / 1e9, "shear_modulus_source": g.source,
            "method": "NACA TN 4197 (Martin), flat uniform fin - ESTIMATED"}
