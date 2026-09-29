"""Editable-field schema for the vehicle designer UI."""

from __future__ import annotations

import math

import numpy as np

from aerodyne.vehicle.components import MATERIALS
from aerodyne.vehicle.vehicle import Vehicle

_N = "number"
MATERIAL_FIELD = {"name": "material", "label": "Material", "type": "select",
                  "options": sorted(MATERIALS)}
COMMON = [{"name": "name", "label": "Name", "type": "text"},
          {"name": "x", "label": "Station (from nose tip)", "type": _N, "unit": "m"},
          {"name": "mass_override", "label": "Measured mass (blank = estimate)", "type": _N,
           "unit": "kg", "optional": True},
          {"name": "cg_override", "label": "Measured CG station (blank = estimate)", "type": _N,
           "unit": "m", "optional": True}]

COMPONENTS = {
    "NoseCone": {"label": "Nose cone", "fields": [
        {"name": "length_", "label": "Length", "type": _N, "unit": "m"},
        {"name": "diameter", "label": "Base diameter", "type": _N, "unit": "m"},
        {"name": "shape", "label": "Shape", "type": "select",
         "options": ["ogive", "conical", "parabolic", "haack", "elliptical"]},
        {"name": "wall_thickness", "label": "Wall thickness", "type": _N, "unit": "m"},
        MATERIAL_FIELD]},
    "BodyTube": {"label": "Body tube", "fields": [
        {"name": "length_", "label": "Length", "type": _N, "unit": "m"},
        {"name": "outer_diameter", "label": "Outer diameter", "type": _N, "unit": "m"},
        {"name": "wall_thickness", "label": "Wall thickness", "type": _N, "unit": "m"},
        MATERIAL_FIELD]},
    "Transition": {"label": "Transition", "fields": [
        {"name": "length_", "label": "Length", "type": _N, "unit": "m"},
        {"name": "fore_diameter", "label": "Fore diameter", "type": _N, "unit": "m"},
        {"name": "aft_diameter", "label": "Aft diameter", "type": _N, "unit": "m"},
        {"name": "wall_thickness", "label": "Wall thickness", "type": _N, "unit": "m"},
        MATERIAL_FIELD]},
    "FinSet": {"label": "Fin set (trapezoidal)", "fields": [
        {"name": "count", "label": "Fin count", "type": "integer"},
        {"name": "root_chord", "label": "Root chord", "type": _N, "unit": "m"},
        {"name": "tip_chord", "label": "Tip chord", "type": _N, "unit": "m"},
        {"name": "span", "label": "Span", "type": _N, "unit": "m"},
        {"name": "sweep", "label": "Leading-edge sweep", "type": _N, "unit": "m"},
        {"name": "thickness", "label": "Thickness", "type": _N, "unit": "m"},
        {"name": "body_radius", "label": "Body radius at fins", "type": _N, "unit": "m"},
        MATERIAL_FIELD]},
    "Bulkhead": {"label": "Bulkhead", "fields": [
        {"name": "diameter", "label": "Diameter", "type": _N, "unit": "m"},
        {"name": "thickness", "label": "Thickness", "type": _N, "unit": "m"},
        MATERIAL_FIELD]},
    "PointMass": {"label": "Mass item", "fields": [
        {"name": "mass_estimate", "label": "Mass estimate", "type": _N, "unit": "kg"},
        {"name": "length_", "label": "Length", "type": _N, "unit": "m"},
        {"name": "radius", "label": "Radius", "type": _N, "unit": "m"},
        {"name": "tags", "label": "Tags (payload, recovery, avionics)", "type": "tags"}]},
    "CadPart": {"label": "CAD part (STEP/STL)", "fields": [
        {"name": "cad_mass", "label": "CAD mass", "type": _N, "unit": "kg"},
        {"name": "cad_cg", "label": "CAD CG station", "type": _N, "unit": "m"},
        {"name": "cad_ixx", "label": "Roll inertia", "type": _N, "unit": "kg·m²"},
        {"name": "cad_iyy", "label": "Pitch inertia", "type": _N, "unit": "kg·m²"},
        {"name": "length_", "label": "Length", "type": _N, "unit": "m"},
        {"name": "source", "label": "Source file", "type": "text"},
        {"name": "tags", "label": "Tags", "type": "tags"}]},
    "MotorSlot": {"label": "Motor slot", "fields": [
        {"name": "motor_length", "label": "Motor length", "type": _N, "unit": "m"},
        {"name": "motor_diameter", "label": "Motor diameter", "type": _N, "unit": "m"}]},
}

RECOVERY_DEVICE = [
    {"name": "name", "label": "Name", "type": "text"},
    {"name": "cd", "label": "Drag coefficient", "type": _N},
    {"name": "diameter", "label": "Diameter", "type": _N, "unit": "m"},
    {"name": "deploy_event", "label": "Deploy at", "type": "select", "options": ["apogee", "altitude"]},
    {"name": "deploy_altitude_agl", "label": "Deploy altitude (AGL)", "type": _N, "unit": "m",
     "optional": True},
    {"name": "delay", "label": "Delay", "type": _N, "unit": "s"},
]

DEFAULTS = {
    "NoseCone": {"name": "Nose cone", "length_": 0.3, "diameter": 0.066, "shape": "ogive",
                 "wall_thickness": 0.0015, "material": "fiberglass"},
    "BodyTube": {"name": "Body tube", "length_": 0.5, "outer_diameter": 0.066, "wall_thickness": 0.0015,
                 "material": "fiberglass"},
    "Transition": {"name": "Transition", "length_": 0.08, "fore_diameter": 0.066, "aft_diameter": 0.054,
                   "wall_thickness": 0.0015, "material": "fiberglass"},
    "FinSet": {"name": "Fins", "count": 3, "root_chord": 0.14, "tip_chord": 0.06, "span": 0.075,
               "sweep": 0.07, "thickness": 0.0024, "body_radius": 0.033, "material": "g10"},
    "Bulkhead": {"name": "Bulkhead", "diameter": 0.063, "thickness": 0.006, "material": "birch_plywood"},
    "PointMass": {"name": "Mass item", "mass_estimate": 0.1, "length_": 0.1, "radius": 0.02, "tags": []},
    "MotorSlot": {"name": "motor", "motor_length": 0.25, "motor_diameter": 0.038},
    "CadPart": {"name": "CAD part", "cad_mass": 0.1, "cad_cg": 0.5, "cad_ixx": 0.0, "cad_iyy": 0.0,
                "length_": 0.1, "source": "", "tags": []},
}


def schema() -> dict:
    return {"components": {k: {"label": v["label"], "fields": COMMON + v["fields"],
                               "defaults": DEFAULTS[k]} for k, v in COMPONENTS.items()},
            "recovery_device": RECOVERY_DEVICE, "materials": {k: m.density for k, m in MATERIALS.items()}}


def _nose_radius(shape: str, x: np.ndarray, L: float, R: float) -> np.ndarray:
    u = np.clip(x / L, 0, 1)
    if shape == "conical":
        return R * u
    if shape == "parabolic":
        return R * (2 * u - u * u)
    if shape == "haack":
        th = np.arccos(1 - 2 * u)
        return R / math.sqrt(math.pi) * np.sqrt(np.maximum(th - np.sin(2 * th) / 2, 0))
    if shape == "elliptical":
        return R * np.sqrt(np.maximum(1 - (1 - u) ** 2, 0))
    rho = (R * R + L * L) / (2 * R)                      # tangent ogive
    return np.sqrt(np.maximum(rho * rho - (L - x) ** 2, 0)) + R - rho


def profile(v: Vehicle) -> list[dict]:
    """Side-view outline shapes (x along the axis, y radial) for the designer drawing."""
    from aerodyne.vehicle.components import (
        BodyTube, Bulkhead, CadPart, FinSet, MotorSlot, NoseCone, PointMass, Transition,
    )

    shapes: list[dict] = []
    for c in v.components:
        if isinstance(c, NoseCone):
            xs = np.linspace(0, c.length_, 40)
            r = _nose_radius(c.shape, xs, c.length_, c.diameter / 2)
            pts = [[c.x + x, y] for x, y in zip(xs, r)] + [[c.x + x, -y] for x, y in zip(xs[::-1], r[::-1])]
            shapes.append({"kind": "body", "name": c.name, "points": pts})
        elif isinstance(c, BodyTube):
            r = c.outer_diameter / 2
            internal = "internal" in c.tags
            shapes.append({"kind": "internal" if internal else "body", "name": c.name,
                           "points": [[c.x, r], [c.x + c.length_, r], [c.x + c.length_, -r], [c.x, -r]]})
        elif isinstance(c, Transition):
            r1, r2 = c.fore_diameter / 2, c.aft_diameter / 2
            shapes.append({"kind": "body", "name": c.name,
                           "points": [[c.x, r1], [c.x + c.length_, r2], [c.x + c.length_, -r2], [c.x, -r1]]})
        elif isinstance(c, FinSet):
            rb = c.body_radius
            tip0 = c.x + c.sweep
            fin = [[c.x, rb], [tip0, rb + c.span], [tip0 + c.tip_chord, rb + c.span], [c.x + c.root_chord, rb]]
            shapes.append({"kind": "fin", "name": c.name, "points": fin})
            shapes.append({"kind": "fin", "name": c.name, "points": [[x, -y] for x, y in fin]})
        elif isinstance(c, MotorSlot):
            r = c.motor_diameter / 2
            shapes.append({"kind": "motor", "name": c.name,
                           "points": [[c.x, r], [c.x + c.motor_length, r], [c.x + c.motor_length, -r], [c.x, -r]]})
        elif isinstance(c, (PointMass, Bulkhead, CadPart)):
            length = max(c.length, 0.004)
            r = getattr(c, "radius", 0.0) or getattr(c, "diameter", 0.0) / 2 or 0.01
            shapes.append({"kind": "mass", "name": c.name,
                           "points": [[c.x, r], [c.x + length, r], [c.x + length, -r], [c.x, -r]]})
    return shapes
