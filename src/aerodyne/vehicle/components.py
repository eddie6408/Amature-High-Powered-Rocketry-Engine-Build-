"""Parametric components.

Axial stations ``x`` are measured in metres from the nose tip, positive aft.
Each component reports its own mass, CG station and inertia about its own CG
(``ixx`` roll axis, ``iyy`` transverse). A measured mass (``mass_override``)
always takes precedence over the geometric estimate and is tagged MEASURED.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from aerodyne.core.provenance import DataKind


@dataclass(frozen=True)
class Material:
    name: str
    density: float           # kg/m^3 (bulk) - for tubes/fins/bulkheads
    tensile_strength: float | None = None   # Pa, for structural margins (nominal)
    youngs_modulus: float | None = None     # Pa
    shear_modulus: float | None = None      # Pa, in-plane (fin flutter); typical handbook value


MATERIALS: dict[str, Material] = {m.name: m for m in [
    Material("fiberglass", 1850.0, 2.4e8, 1.7e10, 2.9e9),
    Material("carbon_fiber", 1600.0, 6.0e8, 7.0e10, 5.0e9),
    Material("blue_tube", 1300.0, None, None),
    Material("cardboard", 680.0, None, None),
    Material("aluminum_6061", 2700.0, 2.76e8, 6.9e10, 2.6e10),
    Material("birch_plywood", 630.0, 4.0e7, 1.1e10, 6.2e8),
    Material("pla", 1240.0, 5.0e7, 3.5e9, 1.3e9),
    Material("abs", 1050.0, 4.0e7, 2.3e9, 8.5e8),
    Material("g10", 1800.0, 2.6e8, 1.8e10, 2.9e9),
]}


BUILTIN_MATERIALS = frozenset(MATERIALS)


def register_material(m: Material) -> str:
    """Make a custom material (e.g. from an OpenRocket file) resolvable by name; returns its key.
    A different material with a taken name is registered as "name [density]"."""
    have = MATERIALS.get(m.name)
    if have is None or have == m:
        MATERIALS[m.name] = m
        return m.name
    key = f"{m.name} [{m.density:g}]"
    MATERIALS[key] = Material(key, m.density, m.tensile_strength, m.youngs_modulus, m.shear_modulus)
    return key


def _material(m: str | Material) -> Material:
    return m if isinstance(m, Material) else MATERIALS[m]


@dataclass
class Component:
    name: str
    x: float                               # fore-end station, m from nose tip
    mass_override: float | None = None     # kg, measured
    cg_override: float | None = None       # m, measured CG station (absolute)
    tags: set[str] = field(default_factory=set)   # e.g. {"recovery", "payload"}
    # external pods / side boosters: identical copies spaced around the axis at radial_offset
    instances: int = 1
    radial_offset: float = 0.0

    # -- to be provided by subclasses -----------------------------------
    def estimated_mass(self) -> float:
        return 0.0

    def local_cg(self) -> float:
        """CG distance aft of the fore end, m."""
        return 0.0

    def inertia_per_mass(self) -> tuple[float, float]:
        """(ixx/m, iyy/m) about own CG, m^2."""
        return (0.0, 0.0)

    @property
    def length(self) -> float:
        return 0.0

    # -- common ------------------------------------------------------------
    @property
    def mass(self) -> float:
        one = self.mass_override if self.mass_override is not None else self.estimated_mass()
        return one * max(1, self.instances)

    @property
    def mass_kind(self) -> DataKind:
        return DataKind.MEASURED if self.mass_override is not None else DataKind.ESTIMATED

    @property
    def cg(self) -> float:
        return self.cg_override if self.cg_override is not None else self.x + self.local_cg()

    def inertia(self) -> tuple[float, float]:
        kx, ky = self.inertia_per_mass()
        r2 = self.radial_offset ** 2          # parallel axis for copies around the axis
        return self.mass * (kx + r2), self.mass * (ky + r2 / 2)


@dataclass
class NoseCone(Component):
    length_: float = 0.3
    diameter: float = 0.1
    shape: str = "ogive"            # ogive | conical | parabolic | haack | elliptical
    wall_thickness: float = 0.002
    material: str | Material = "fiberglass"

    @property
    def length(self) -> float:
        return self.length_

    def _fill(self) -> float:
        return {"conical": 1 / 3, "ogive": 0.53, "parabolic": 0.5, "haack": 0.5,
                "elliptical": 2 / 3}.get(self.shape, 0.5)

    def estimated_mass(self) -> float:
        r = self.diameter / 2
        slant = math.hypot(self.length_, r)
        # shell area: cone lateral area scaled toward cylinder for fuller shapes
        area = math.pi * r * slant * (1.0 + 0.6 * (self._fill() - 1 / 3))
        return area * self.wall_thickness * _material(self.material).density

    def local_cg(self) -> float:
        # thin-shell CG: 2/3 L for a cone, closer to the base for blunter shapes
        return self.length_ * (2 / 3 - 0.1 * (self._fill() - 1 / 3))

    def inertia_per_mass(self) -> tuple[float, float]:
        r = self.diameter / 2
        return 0.5 * r ** 2, 0.25 * r ** 2 + self.length_ ** 2 / 18


@dataclass
class BodyTube(Component):
    length_: float = 0.5
    outer_diameter: float = 0.1
    wall_thickness: float = 0.002
    material: str | Material = "fiberglass"

    @property
    def length(self) -> float:
        return self.length_

    @property
    def inner_diameter(self) -> float:
        return self.outer_diameter - 2 * self.wall_thickness

    def estimated_mass(self) -> float:
        ro, ri = self.outer_diameter / 2, self.inner_diameter / 2
        return math.pi * (ro ** 2 - ri ** 2) * self.length_ * _material(self.material).density

    def local_cg(self) -> float:
        return self.length_ / 2

    def inertia_per_mass(self) -> tuple[float, float]:
        ro, ri = self.outer_diameter / 2, self.inner_diameter / 2
        return 0.5 * (ro ** 2 + ri ** 2), (3 * (ro ** 2 + ri ** 2) + self.length_ ** 2) / 12


@dataclass
class Transition(Component):
    length_: float = 0.1
    fore_diameter: float = 0.1
    aft_diameter: float = 0.075
    wall_thickness: float = 0.002
    material: str | Material = "fiberglass"

    @property
    def length(self) -> float:
        return self.length_

    def estimated_mass(self) -> float:
        r1, r2 = self.fore_diameter / 2, self.aft_diameter / 2
        slant = math.hypot(self.length_, r1 - r2)
        return math.pi * (r1 + r2) * slant * self.wall_thickness * _material(self.material).density

    def local_cg(self) -> float:
        r1, r2 = self.fore_diameter / 2, self.aft_diameter / 2
        return self.length_ * (r1 + 2 * r2) / (3 * (r1 + r2))

    def inertia_per_mass(self) -> tuple[float, float]:
        r = (self.fore_diameter + self.aft_diameter) / 4
        return r ** 2, 0.5 * r ** 2 + self.length_ ** 2 / 12


@dataclass
class FinSet(Component):
    """Trapezoidal fin set. ``x`` is the root-chord leading-edge station."""

    count: int = 3
    root_chord: float = 0.15
    tip_chord: float = 0.05
    span: float = 0.1
    sweep: float = 0.08            # leading-edge sweep distance at the tip, m
    thickness: float = 0.003
    body_radius: float = 0.05      # radius of the tube the fins attach to
    material: str | Material = "g10"
    cant_deg: float = 0.0
    cross_section: str = "rounded"   # square | rounded | airfoil (edge shape, drives pressure drag)
    shear_modulus_gpa: float | None = None   # measured/declared in-plane shear modulus (fin flutter)

    @property
    def length(self) -> float:
        return max(self.root_chord, self.sweep + self.tip_chord)

    @property
    def planform_area(self) -> float:
        return 0.5 * (self.root_chord + self.tip_chord) * self.span

    @property
    def wetted_area(self) -> float:
        return 2 * self.count * self.planform_area

    @property
    def frontal_area(self) -> float:
        return self.count * self.span * self.thickness

    def estimated_mass(self) -> float:
        return self.count * self.planform_area * self.thickness * _material(self.material).density

    def local_cg(self) -> float:
        a, b, m = self.root_chord, self.tip_chord, self.sweep
        return (a * a + a * b + b * b + m * (a + 2 * b)) / (3 * (a + b))

    def inertia_per_mass(self) -> tuple[float, float]:
        # planform centroid distance from the root chord
        a, b, s = self.root_chord, self.tip_chord, self.span
        ybar = s * (a + 2 * b) / (3 * (a + b))
        r = self.body_radius + ybar
        ixx = r ** 2 + s ** 2 / 18
        iyy = 0.5 * ixx + (a ** 2) / 18
        return ixx, iyy


@dataclass
class TubeFinSet(FinSet):
    """Tube fins: ``count`` open tubes of length ``root_chord`` and outer radius ``span / 2``
    around the body. For lift they are treated as flat fins of the same side-on planform
    (length x tube diameter), an ESTIMATE; mass, wetted and frontal areas are the tubes' own."""

    cross_section: str = "square"

    def __post_init__(self) -> None:
        self.tip_chord = self.root_chord
        self.sweep = 0.0

    @property
    def outer_radius(self) -> float:
        return self.span / 2

    @property
    def wetted_area(self) -> float:
        ro, ri = self.outer_radius, max(self.outer_radius - self.thickness, 0.0)
        return self.count * 2 * math.pi * (ro + ri) * self.root_chord

    @property
    def frontal_area(self) -> float:
        ro, ri = self.outer_radius, max(self.outer_radius - self.thickness, 0.0)
        return self.count * math.pi * (ro * ro - ri * ri)

    def estimated_mass(self) -> float:
        return self.frontal_area * self.root_chord * _material(self.material).density

    def local_cg(self) -> float:
        return self.root_chord / 2

    def inertia_per_mass(self) -> tuple[float, float]:
        rc = self.body_radius + self.outer_radius
        ro, ri = self.outer_radius, max(self.outer_radius - self.thickness, 0.0)
        ixx = rc ** 2 + (ro * ro + ri * ri) / 2
        return ixx, 0.5 * ixx + self.root_chord ** 2 / 12


@dataclass
class Bulkhead(Component):
    diameter: float = 0.1
    thickness: float = 0.006
    material: str | Material = "birch_plywood"

    @property
    def length(self) -> float:
        return self.thickness

    def estimated_mass(self) -> float:
        return math.pi * (self.diameter / 2) ** 2 * self.thickness * _material(self.material).density

    def local_cg(self) -> float:
        return self.thickness / 2

    def inertia_per_mass(self) -> tuple[float, float]:
        r = self.diameter / 2
        return 0.5 * r ** 2, 0.25 * r ** 2 + self.thickness ** 2 / 12


@dataclass
class PointMass(Component):
    """Generic mass item: avionics sled, payload, parachute, shock cord, ballast,
    coupler, motor retainer... ``length_``/``radius`` approximate its extent."""

    mass_estimate: float = 0.1
    length_: float = 0.0
    radius: float = 0.0

    @property
    def length(self) -> float:
        return self.length_

    def estimated_mass(self) -> float:
        return self.mass_estimate

    def local_cg(self) -> float:
        return self.length_ / 2

    def inertia_per_mass(self) -> tuple[float, float]:
        return 0.5 * self.radius ** 2, (3 * self.radius ** 2 + self.length_ ** 2) / 12


@dataclass
class MotorSlot(Component):
    """Where the motor sits. Its mass comes from the MotorPerformance at time t,
    so the dry vehicle never double-counts motor mass."""

    motor_length: float = 0.25
    motor_diameter: float = 0.038

    @property
    def length(self) -> float:
        return self.motor_length

    def local_cg(self) -> float:
        return self.motor_length / 2

    def inertia_per_mass(self) -> tuple[float, float]:
        r = self.motor_diameter / 2
        return 0.5 * r ** 2, (3 * r ** 2 + self.motor_length ** 2) / 12


@dataclass
class CadPart(Component):
    """Part whose mass properties come from CAD (STL mesh x density, or a CAD
    mass-property report). ESTIMATED unless ``mass_override`` is set."""

    cad_mass: float = 0.0
    cad_cg: float = 0.0            # absolute station, m
    cad_ixx: float = 0.0           # kg m^2 about own CG
    cad_iyy: float = 0.0
    length_: float = 0.0
    source: str = ""
    source_sha256: str = ""

    @property
    def length(self) -> float:
        return self.length_

    def estimated_mass(self) -> float:
        return self.cad_mass

    def local_cg(self) -> float:
        return self.cad_cg - self.x

    def inertia_per_mass(self) -> tuple[float, float]:
        if self.cad_mass <= 0:
            return (0.0, 0.0)
        return self.cad_ixx / self.cad_mass, self.cad_iyy / self.cad_mass
