"""Vehicle: an ordered set of components plus the motor slot."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from aerodyne.core.provenance import stable_hash
from aerodyne.vehicle import components as comp
from aerodyne.vehicle.components import (
    BodyTube,
    Component,
    FinSet,
    Material,
    MotorSlot,
    NoseCone,
    Transition,
)


@dataclass
class Vehicle:
    name: str
    components: list[Component] = field(default_factory=list)
    motor_slot: MotorSlot | None = None
    launch_lug_drag_area: float = 0.0     # m^2 extra drag area (rail buttons etc.)
    surface_roughness: float = 20e-6      # m, painted surface

    def add(self, c: Component) -> Component:
        self.components.append(c)
        if isinstance(c, MotorSlot) and ("pod" not in c.tags or self.motor_slot is None):
            self.motor_slot = c
        return c

    # ---- geometry ---------------------------------------------------------
    @staticmethod
    def on_axis(c: Component) -> bool:
        """Part of the main airframe (not an internal tube, not an external pod / booster)."""
        return "internal" not in c.tags and "pod" not in c.tags

    @property
    def length(self) -> float:
        return max((c.x + c.length for c in self.components
                    if isinstance(c, (NoseCone, BodyTube, Transition)) and self.on_axis(c)),
                   default=0.0)

    @property
    def reference_diameter(self) -> float:
        ds = [c.diameter for c in self.components if isinstance(c, NoseCone) and self.on_axis(c)]
        ds += [c.outer_diameter for c in self.components if isinstance(c, BodyTube) and self.on_axis(c)]
        ds += [max(c.fore_diameter, c.aft_diameter) for c in self.components
               if isinstance(c, Transition) and self.on_axis(c)]
        if not ds:
            raise ValueError("vehicle has no body components")
        return max(ds)

    @property
    def reference_area(self) -> float:
        import math

        return math.pi * self.reference_diameter ** 2 / 4

    @property
    def stages(self) -> list[int]:
        return sorted({c.stage for c in self.components})

    def motor_slot_for(self, stage: int) -> MotorSlot | None:
        """The motor slot of a stage (the vehicle's own slot for stage 0 if none is tagged)."""
        slots = [c for c in self.components if isinstance(c, MotorSlot) and c.stage == stage]
        return slots[-1] if slots else (self.motor_slot if stage == 0 else None)

    def subset(self, stages: set[int] | frozenset[int]) -> "Vehicle":
        """The rocket made of these stages only (after the others have separated)."""
        v = Vehicle(name=self.name, launch_lug_drag_area=self.launch_lug_drag_area if 0 in stages else 0.0,
                    surface_roughness=self.surface_roughness)
        for c in self.components:
            if c.stage in stages:
                v.add(c)
        return v

    def nose(self) -> NoseCone | None:
        return next((c for c in self.components if isinstance(c, NoseCone) and self.on_axis(c)), None)

    def pod_noses(self) -> list[NoseCone]:
        return [c for c in self.components if isinstance(c, NoseCone) and "pod" in c.tags]

    def pod_groups(self) -> dict[str, list[Component]]:
        """External pods / boosters by group (tag ``pod:<name>``)."""
        out: dict[str, list[Component]] = {}
        for c in self.components:
            if "pod" in c.tags:
                key = next((t for t in c.tags if t.startswith("pod:")), "pod:?")
                out.setdefault(key, []).append(c)
        return out

    def fin_sets(self) -> list[FinSet]:
        return [c for c in self.components if isinstance(c, FinSet)]

    def bodies(self) -> list[BodyTube]:
        return [c for c in self.components if isinstance(c, BodyTube)]

    def transitions(self) -> list[Transition]:
        return [c for c in self.components if isinstance(c, Transition)]

    def aft_diameter(self) -> float:
        """Diameter at the aft end (for base drag)."""
        aft = max((c for c in self.components if isinstance(c, (BodyTube, Transition, NoseCone))
                   and self.on_axis(c)), key=lambda c: c.x + c.length)
        if isinstance(aft, BodyTube):
            return aft.outer_diameter
        if isinstance(aft, Transition):
            return aft.aft_diameter
        return aft.diameter

    def validate(self) -> list[str]:
        """Geometric sanity checks. Returns a list of problems (empty = OK)."""
        problems = []
        if self.nose() is None:
            problems.append("no nose cone")
        if not self.fin_sets():
            problems.append("no fin set (vehicle will be aerodynamically unstable)")
        if self.motor_slot is None:
            problems.append("no motor slot")
        for c in self.components:
            if c.mass < 0:
                problems.append(f"{c.name}: negative mass")
            if c.x < 0:
                problems.append(f"{c.name}: station before nose tip")
        return problems

    # ---- serialization (digital twin / configuration hash) ---------------
    def to_dict(self) -> dict[str, Any]:
        # optional fields added later are written only when set, so existing designs keep their hash
        optional = {"instances": 1, "radial_offset": 0.0, "shear_modulus_gpa": None, "stage": 0}
        custom: dict[str, dict[str, Any]] = {}

        def enc(c: Component) -> dict[str, Any]:
            d = {}
            for f in dataclasses.fields(c):
                v = getattr(c, f.name)
                if f.name in optional and v == optional[f.name]:
                    continue
                if isinstance(v, Material):
                    v = comp.register_material(v)
                if f.name == "material" and isinstance(v, str) and v not in comp.BUILTIN_MATERIALS and v in comp.MATERIALS:
                    m = comp.MATERIALS[v]
                    custom[v] = {"density": m.density, "tensile_strength": m.tensile_strength,
                                 "youngs_modulus": m.youngs_modulus, "shear_modulus": m.shear_modulus}
                elif isinstance(v, set):
                    v = sorted(v)
                d[f.name] = v
            return {"type": type(c).__name__, **d}

        out = {"name": self.name, "components": [enc(c) for c in self.components],
               "launch_lug_drag_area": self.launch_lug_drag_area,
               "surface_roughness": self.surface_roughness}
        if custom:
            out["materials"] = custom
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Vehicle":
        v = cls(name=data["name"], launch_lug_drag_area=data.get("launch_lug_drag_area", 0.0),
                surface_roughness=data.get("surface_roughness", 20e-6))
        for name, m in (data.get("materials") or {}).items():
            comp.MATERIALS.setdefault(name, Material(name, **m))
        for cd in data["components"]:
            cd = dict(cd)
            klass = getattr(comp, cd.pop("type"))
            if "tags" in cd:
                cd["tags"] = set(cd["tags"])
            v.add(klass(**cd))
        return v

    @property
    def config_hash(self) -> str:
        return stable_hash(self.to_dict())
