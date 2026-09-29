"""Analytical aerodynamic model (ESTIMATED).

* Normal force / centre of pressure: Barrowman (1967) with Prandtl-Glauert
  compressibility on fins; valid for small angles of attack, subsonic and low
  supersonic. Fidelity degrades above ~Mach 1.5 - use table data there.
* Zero-lift drag: component build-up (skin friction with roughness limit,
  body/fin form factors, base drag, fin leading-edge and nose pressure drag,
  transonic blending) in the style of the OpenRocket technical documentation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from aerodyne.aero.model import AeroCoefficients, FlightCondition
from aerodyne.core.provenance import DataKind
from aerodyne.vehicle.components import FinSet, NoseCone
from aerodyne.vehicle.vehicle import Vehicle

_NOSE_CP = {"conical": 2 / 3, "ogive": 0.466, "parabolic": 0.5, "haack": 0.437,
            "elliptical": 1 / 3}
_NOSE_WAVE = {"conical": 1.0, "ogive": 0.75, "parabolic": 0.6, "haack": 0.5, "elliptical": 1.2}


def _beta(mach: float) -> float:
    return max(math.sqrt(abs(1.0 - mach * mach)), 0.3)


def fin_cn_alpha(f: FinSet, d_ref: float, mach: float = 0.0) -> float:
    a, b, s, m = f.root_chord, f.tip_chord, f.span, f.sweep
    lm = math.hypot(s, m + b / 2 - a / 2)
    interference = 1.0 + f.body_radius / (s + f.body_radius)
    n = f.count
    # Barrowman is derived for 3-4 fins; for more fins the gain saturates
    n_eff = n if n <= 4 else 4 + 0.5 * (n - 4)
    cna = interference * (4 * n_eff * (s / d_ref) ** 2) / (1 + math.sqrt(1 + (2 * lm / (a + b)) ** 2))
    return cna / _beta(mach) if mach < 1.0 else cna * math.sqrt(0.75) / _beta(mach)


def fin_cp(f: FinSet) -> float:
    a, b, m = f.root_chord, f.tip_chord, f.sweep
    return f.x + m * (a + 2 * b) / (3 * (a + b)) + (a + b - a * b / (a + b)) / 6


def barrowman_cp(vehicle: Vehicle, mach: float = 0.0) -> tuple[float, float, list[tuple[str, float, float]]]:
    """Return (cn_alpha_total, xcp, [(name, cn_alpha, x)])."""
    d_ref = vehicle.reference_diameter
    parts: list[tuple[str, float, float]] = []
    nose = vehicle.nose()
    if nose is not None:
        cna = 2.0 * (nose.diameter / d_ref) ** 2
        parts.append((nose.name, cna, nose.x + _NOSE_CP.get(nose.shape, 0.5) * nose.length_))
    for t in vehicle.transitions():
        d1, d2 = t.fore_diameter, t.aft_diameter
        cna = 2.0 * ((d2 / d_ref) ** 2 - (d1 / d_ref) ** 2)
        r = d1 / d2
        xt = t.x + t.length_ / 3 * (1 + (1 - r) / (1 - r * r)) if abs(1 - r * r) > 1e-9 else t.x
        parts.append((t.name, cna, xt))
    for f in vehicle.fin_sets():
        parts.append((f.name, fin_cn_alpha(f, d_ref, mach), fin_cp(f)))
    total = sum(p[1] for p in parts)
    if total == 0:
        raise ValueError("vehicle has no lifting surfaces")
    xcp = sum(p[1] * p[2] for p in parts) / total
    return total, xcp, parts


def _skin_friction(re: float, rough_rel: float, mach: float) -> float:
    re = max(re, 1e4)
    cf_turb = 1.0 / (1.50 * math.log(re) - 5.6) ** 2
    cf_rough = 0.032 * max(rough_rel, 1e-9) ** 0.2
    cf = max(cf_turb, cf_rough)
    if mach < 1.0:
        return cf * (1 - 0.1 * mach * mach)
    return cf / (1 + 0.15 * mach * mach) ** 0.58


def _base_cd(mach: float) -> float:
    if mach < 1.0:
        return 0.12 + 0.13 * mach * mach
    return 0.25 / mach


def _le_pressure_cd(mach: float) -> float:
    """Rounded fin leading edge stagnation pressure drag."""
    if mach < 0.9:
        return (1 - mach * mach) ** -0.417 - 1
    if mach < 1.0:
        return 1 - 1.785 * (mach - 0.9)
    return 1.214 - 0.502 / mach ** 2 + 0.1095 / mach ** 4


def _stagnation_cd(mach: float) -> float:
    """Pressure coefficient on a blunt face (stagnation), subsonic/supersonic fit."""
    if mach < 1.0:
        return 0.85 * (1 + mach * mach / 4 + mach ** 4 / 40)
    return 0.85 * (1.84 - 0.76 / mach ** 2 + 0.166 / mach ** 4 + 0.035 / mach ** 6)


def _fin_edge_cd(section: str, mach: float) -> float:
    """Leading + trailing edge pressure drag per unit fin frontal area."""
    if section == "square":
        return _stagnation_cd(mach) + _base_cd(mach)
    if section == "airfoil":
        return _le_pressure_cd(mach)                       # rounded LE, sharp TE
    return _le_pressure_cd(mach) + 0.5 * _base_cd(mach)    # rounded LE and TE


def _smoothstep(x: float, x0: float, x1: float) -> float:
    u = min(max((x - x0) / (x1 - x0), 0.0), 1.0)
    return u * u * (3 - 2 * u)


def _nose_wave_cd(nose: NoseCone, mach: float) -> float:
    theta = math.atan(nose.diameter / 2 / nose.length_)
    shape = _NOSE_WAVE.get(nose.shape, 0.75)

    def sup(m: float) -> float:
        return shape * (2.1 * math.sin(theta) ** 2 + 0.5 * math.sin(theta) / math.sqrt(m * m - 1))

    if mach <= 0.8:
        return 0.0
    if mach < 1.2:
        return sup(1.2) * _smoothstep(mach, 0.8, 1.2)
    return sup(mach)


@dataclass
class AnalyticalAeroModel:
    vehicle: Vehicle

    def __post_init__(self) -> None:
        self.reference_diameter = self.vehicle.reference_diameter
        self.reference_area = self.vehicle.reference_area
        v = self.vehicle
        self._length = v.length
        self._fineness = self._length / self.reference_diameter
        # only airframe surfaces see the flow - internal tubes (motor mounts) are excluded
        wet = sum(math.pi * b.outer_diameter * b.length_ for b in v.bodies() if "internal" not in b.tags)
        n = v.nose()
        if n is not None:
            wet += math.pi * n.diameter / 2 * math.hypot(n.length_, n.diameter / 2) * 1.1
        for t in v.transitions():
            wet += math.pi * (t.fore_diameter + t.aft_diameter) / 2 * math.hypot(
                t.length_, (t.fore_diameter - t.aft_diameter) / 2)
        self._wet_body = wet
        self._wet_fins = sum(2 * f.count * f.planform_area for f in v.fin_sets())
        self._fin_frontal = sum(f.count * f.span * f.thickness for f in v.fin_sets())
        self._fin_frontal_by_section = [(f.count * f.span * f.thickness, f.cross_section) for f in v.fin_sets()]
        self._fin_tc = max((f.thickness / f.root_chord for f in v.fin_sets()), default=0.0)
        self._mac = max((f.root_chord for f in v.fin_sets()), default=0.1)
        self._base_area = math.pi * v.aft_diameter() ** 2 / 4

    def drag_breakdown(self, mach: float, reynolds_per_m: float, thrusting: bool = False) -> dict[str, float]:
        """Zero-lift drag split into friction, pressure and base (same split OpenRocket reports)."""
        a_ref = self.reference_area
        rough = self.vehicle.surface_roughness
        cf_b = _skin_friction(reynolds_per_m * self._length, rough / self._length, mach)
        cf_f = _skin_friction(reynolds_per_m * self._mac, rough / self._mac, mach)
        body_form = 1 + 1 / (2 * self._fineness)
        fin_form = 1 + 2 * self._fin_tc
        cd_fric = (cf_b * body_form * self._wet_body + cf_f * fin_form * self._wet_fins) / a_ref
        base_area = self._base_area
        if thrusting and self.vehicle.motor_slot is not None:
            # motor exhaust fills the nozzle area; approximate with motor case area
            base_area = max(0.0, base_area - math.pi * self.vehicle.motor_slot.motor_diameter ** 2 / 4)
        cd_base = _base_cd(mach) * base_area / a_ref
        cd_fin_p = sum(area * _fin_edge_cd(sec, mach) for area, sec in self._fin_frontal_by_section) / a_ref
        nose = self.vehicle.nose()
        cd_nose = _nose_wave_cd(nose, mach) * (nose.diameter / self.reference_diameter) ** 2 if nose else 0
        cd_para = 1.2 * self.vehicle.launch_lug_drag_area / a_ref
        return {"friction": cd_fric, "pressure": cd_fin_p + cd_nose + cd_para, "base": cd_base}

    def zero_lift_cd(self, mach: float, reynolds_per_m: float, thrusting: bool = False) -> float:
        return sum(self.drag_breakdown(mach, reynolds_per_m, thrusting).values())

    def coefficients(self, cond: FlightCondition) -> AeroCoefficients:
        cna, xcp, parts = barrowman_cp(self.vehicle, cond.mach)
        fins = [p for p in parts if any(p[0] == f.name for f in self.vehicle.fin_sets())]
        fin_cna = sum(p[1] for p in fins)
        fin_x = sum(p[1] * p[2] for p in fins) / fin_cna if fin_cna else None
        a = cond.alpha
        cn = cna * math.sin(a)
        cd0 = self.zero_lift_cd(cond.mach, cond.reynolds_per_m, cond.thrusting)
        # axial/normal -> wind axes at small alpha
        cd = cd0 * math.cos(a) + cn * math.sin(a)
        cl = cn * math.cos(a) - cd0 * math.sin(a)
        cm = -cn * xcp / self.reference_diameter
        return AeroCoefficients(cd=cd, ca=cd0, cn_alpha=cna, xcp=xcp, cl=cl, cm=cm, cn=cn,
                                fin_station=fin_x, fin_cn_alpha=fin_cna,
                                kind=DataKind.ESTIMATED, source="analytical/barrowman")
