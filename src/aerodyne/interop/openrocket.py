"""OpenRocket ``.ork`` design import.

An ``.ork`` file is a ZIP (or gzip, or plain XML) containing an OpenRocket XML
document. Supported: nose cone, body tube, transition, trapezoidal fin set,
inner tube / motor mount (with motor configuration), bulkhead, centering ring,
tube coupler, mass component, parachute, streamer, shock cord, launch lug,
rail button. Positions are resolved from OpenRocket's relative placement
(``top``/``middle``/``bottom``/``after``/``absolute``, both the older
``<position type=...>`` and the newer ``<axialoffset method=...>`` syntax)
and ``auto`` radii are resolved from neighbouring components.

Anything unsupported is reported in ``warnings`` - never silently dropped.
"""

from __future__ import annotations

import gzip
import io
import math
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from aerodyne.recovery.recovery import RecoveryConfig, RecoveryDevice
from aerodyne.vehicle.components import (
    BodyTube,
    FinSet,
    Material,
    MotorSlot,
    NoseCone,
    PointMass,
    Transition,
)
from aerodyne.vehicle.vehicle import Vehicle

_SHAPES = {"ogive": "ogive", "conical": "conical", "parabolic": "parabolic", "haack": "haack",
           "ellipsoid": "elliptical", "power": "parabolic"}
_MASS_ONLY = {"masscomponent", "shockcord", "streamer", "launchlug", "railbutton",
              "centeringring", "tubecoupler", "parachute", "bulkhead", "innertube"}
_SUPPORTED = {"nosecone", "bodytube", "transition", "trapezoidfinset", "stage", *_MASS_ONLY}


@dataclass
class MotorRef:
    manufacturer: str
    designation: str
    diameter_m: float | None
    length_m: float | None
    delay: str | None
    config_id: str | None


@dataclass
class OrkImport:
    vehicle: Vehicle
    recovery: RecoveryConfig | None
    motors: list[MotorRef]
    warnings: list[str] = field(default_factory=list)
    creator: str = ""


def _load_xml(path: str | Path) -> ET.Element:
    raw = Path(path).read_bytes()
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = next((n for n in z.namelist() if n.endswith(".ork") or n.endswith(".xml")),
                        z.namelist()[0])
            raw = z.read(name)
    elif raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)


def _f(el: ET.Element, tag: str, default: float | None = None) -> float | None:
    x = el.find(tag)
    if x is None or x.text is None or x.text.strip() in ("", "auto"):
        return default
    t = x.text.strip()
    return float(t.split()[-1]) if t.startswith("auto") else float(t)


def _auto(el: ET.Element, tag: str) -> bool:
    x = el.find(tag)
    return x is not None and x.text is not None and x.text.strip().startswith("auto")


def _material(el: ET.Element) -> Material | None:
    m = el.find("material")
    if m is None:
        return None
    return Material(name=(m.text or "unnamed").strip(), density=float(m.get("density", "0")))


def _placement(el: ET.Element) -> tuple[str, float]:
    ax = el.find("axialoffset")
    if ax is not None:
        return ax.get("method", "top"), float(ax.text or 0)
    pos = el.find("position")
    if pos is not None:
        return pos.get("type", "top"), float(pos.text or 0)
    return "after", 0.0


class _Builder:
    def __init__(self) -> None:
        self.v = Vehicle(name="imported")
        self.warnings: list[str] = []
        self.chutes: list[RecoveryDevice] = []
        self.motors: list[MotorRef] = []
        self.pending_auto: list[tuple[object, str]] = []
        self.stack: list[tuple[float, float, float]] = []   # (fore x, aft x, aft radius or nan)
        self.tubes: list[tuple[BodyTube, ET.Element]] = []  # children resolved after auto radii

    # ---- placement -----------------------------------------------------------
    @staticmethod
    def _x(method: str, off: float, length: float, p_fore: float, p_len: float, after: float) -> float:
        if method == "top":
            return p_fore + off
        if method == "bottom":
            return p_fore + p_len - length + off
        if method == "middle":
            return p_fore + (p_len - length) / 2 + off
        if method == "absolute":
            return off
        return after + off   # "after"

    # ---- traversal -------------------------------------------------------------
    def build(self, root: ET.Element) -> None:
        rocket = root.find("rocket")
        if rocket is None:
            raise ValueError("no <rocket> element")
        self.v.name = (rocket.findtext("name") or "imported").strip()
        x = 0.0
        stages = rocket.findall("./subcomponents/stage")
        if len(stages) > 1:
            self.warnings.append(f"{len(stages)} stages: imported as one stack; staging not modelled")
        for st in stages:
            x = self._children(st, fore=x, length=0.0, radius=math.nan, top_level=True)
        self._resolve_auto()
        # body-tube children need the resolved radius (fins, rings with 'auto' radii)
        for tube, el in self.tubes:
            ro = tube.outer_diameter / 2
            self._children(el, tube.x, tube.length_, ro, airframe_ri=ro - tube.wall_thickness)
        self._resolve_auto()

    def _children(self, parent: ET.Element, fore: float, length: float, radius: float,
                  top_level: bool = False, airframe_ri: float = math.nan) -> float:
        sub = parent.find("subcomponents")
        after = fore
        if sub is None:
            return after
        for el in sub:
            tag = el.tag
            if tag not in _SUPPORTED:
                self.warnings.append(f"unsupported component <{tag}> '{el.findtext('name')}' skipped")
                continue
            if tag == "stage":
                after = self._children(el, after, 0.0, radius, top_level=True)
                continue
            method, off = _placement(el)
            if top_level:
                method, off = "after", 0.0
            clen = self._length(el)
            cx = self._x(method, off, clen, fore, length, after)
            self._component(el, cx, clen, radius, airframe_ri)
            if top_level:
                after = cx + clen
        return after

    @staticmethod
    def _length(el: ET.Element) -> float:
        if el.tag == "trapezoidfinset":
            return _f(el, "rootchord", 0.0)
        if el.tag == "bulkhead":
            return _f(el, "length", _f(el, "thickness", 0.0))
        return _f(el, "length", 0.0) or 0.0

    def _override(self, comp, el: ET.Element) -> None:
        m = _f(el, "overridemass")
        if m is not None:
            comp.mass_override = m
            self.warnings.append(f"'{comp.name}': OpenRocket mass override {m:.4f} kg imported as "
                                 "MEASURED - confirm it was weighed")
            if (el.findtext("overridesubcomponentsmass") or "").strip() == "true":
                self.warnings.append(f"'{comp.name}': override includes subcomponents in OpenRocket; "
                                     "AERODYNE adds child masses separately - check totals")
        cg = _f(el, "overridecg")
        if cg is not None:
            comp.cg_override = comp.x + cg

    def _component(self, el: ET.Element, x: float, length: float, parent_r: float,
                   airframe_ri: float = math.nan) -> None:
        name = (el.findtext("name") or el.tag).strip()
        tag = el.tag
        mat = _material(el)
        if tag == "nosecone":
            r = _f(el, "aftradius", math.nan)
            shape = _SHAPES.get((el.findtext("shape") or "ogive").strip(), "ogive")
            if (el.findtext("shape") or "").strip() == "haack" and (_f(el, "shapeparameter", 0) or 0) > 0.2:
                self.warnings.append(f"'{name}': LV-Haack treated as Von Karman for CP")
            c = NoseCone(name, x=x, length_=length, diameter=2 * r if r == r else 0.0, shape=shape,
                         wall_thickness=_f(el, "thickness", 0.002), material=mat or "fiberglass")
            if _auto(el, "aftradius") or r != r:
                self.pending_auto.append((c, "diameter"))
            self._override(c, el)
            self.v.add(c)
            self.stack.append((x, x + length, r))
            self._children(el, x, length, r)
        elif tag == "bodytube":
            r = _f(el, "radius", _f(el, "outerradius", math.nan))
            c = BodyTube(name, x=x, length_=length, outer_diameter=2 * r if r == r else 0.0,
                         wall_thickness=_f(el, "thickness", 0.001), material=mat or "fiberglass")
            if _auto(el, "radius") or r != r:
                self.pending_auto.append((c, "outer_diameter"))
            self._override(c, el)
            self.v.add(c)
            self.stack.append((x, x + length, r))
            self.tubes.append((c, el))
        elif tag == "transition":
            r1, r2 = _f(el, "foreradius", math.nan), _f(el, "aftradius", math.nan)
            c = Transition(name, x=x, length_=length, fore_diameter=2 * r1 if r1 == r1 else 0.0,
                           aft_diameter=2 * r2 if r2 == r2 else 0.0,
                           wall_thickness=_f(el, "thickness", 0.002), material=mat or "fiberglass")
            if _auto(el, "foreradius") or r1 != r1:
                self.pending_auto.append((c, "fore_diameter"))
            if _auto(el, "aftradius") or r2 != r2:
                self.pending_auto.append((c, "aft_diameter"))
            self._override(c, el)
            self.v.add(c)
            self.stack.append((x, x + length, r2))
            self._children(el, x, length, r2)
        elif tag == "trapezoidfinset":
            if (_f(el, "cant", 0) or 0) != 0:
                self.warnings.append(f"'{name}': fin cant not modelled")
            c = FinSet(name, x=x, count=int(_f(el, "fincount", 3)), root_chord=length,
                       tip_chord=_f(el, "tipchord", 0.0), span=_f(el, "height", 0.0),
                       sweep=_f(el, "sweeplength", 0.0), thickness=_f(el, "thickness", 0.003),
                       body_radius=parent_r if parent_r == parent_r else 0.0,
                       material=mat or "g10")
            if parent_r != parent_r:
                self.pending_auto.append((c, "body_radius"))
            self._override(c, el)
            self.v.add(c)
        elif tag == "innertube":
            ro = _f(el, "outerradius", 0.0)
            c = BodyTube(name, x=x, length_=length, outer_diameter=2 * ro,
                         wall_thickness=_f(el, "thickness", 0.0005), material=mat or "cardboard",
                         tags={"internal"})
            self._override(c, el)
            self.v.add(c)
            mm = el.find("motormount")
            if mm is not None:
                self._motor_mount(mm, x, length, 2 * ro)
            self._children(el, x, length, ro, airframe_ri=airframe_ri)
        elif tag in ("bulkhead", "centeringring", "tubecoupler"):
            # 'auto' outer radius = inside of the enclosing airframe; 'auto' inner radius
            # of a centering ring = outside of the tube it sits on
            ro = _f(el, "outerradius", airframe_ri if airframe_ri == airframe_ri else parent_r)
            ro = ro if ro == ro else 0.0
            ri = _f(el, "innerradius", parent_r if tag == "centeringring" and parent_r == parent_r
                    and parent_r < ro else 0.0) or 0.0
            if tag == "tubecoupler":
                ri = ro - (_f(el, "thickness", 0.001) or 0.001)
            dens = mat.density if mat else 630.0
            mass = math.pi * (ro * ro - ri * ri) * length * dens
            c = PointMass(name, x=x, mass_estimate=mass, length_=length, radius=ro)
            self._override(c, el)
            self.v.add(c)
            self._children(el, x, length, ro, airframe_ri=airframe_ri)
        elif tag in ("masscomponent", "shockcord", "streamer", "parachute", "launchlug", "railbutton"):
            mass = _f(el, "mass")
            if mass is None:
                mass = self._surface_mass(el, mat)
            rad = _f(el, "packedradius", _f(el, "outerradius", 0.0)) or 0.0
            tags = {"recovery"} if tag in ("parachute", "streamer", "shockcord") else set()
            c = PointMass(name, x=x, mass_estimate=mass, length_=length, radius=rad, tags=tags)
            self._override(c, el)
            self.v.add(c)
            if tag == "parachute":
                self._parachute(el, name)
            elif tag == "launchlug":
                self.v.launch_lug_drag_area += math.pi * rad * rad

    def _surface_mass(self, el: ET.Element, mat: Material | None) -> float:
        if el.tag == "parachute" and mat is not None:
            d = _f(el, "diameter", 0.0)
            m = math.pi * d * d / 4 * mat.density
            lc, ll = _f(el, "linecount", 0) or 0, _f(el, "linelength", 0) or 0
            lm = el.find("linematerial")
            if lm is not None:
                m += lc * ll * float(lm.get("density", "0"))
            return m
        if el.tag == "shockcord" and mat is not None:
            return (_f(el, "cordlength", 0.0) or 0.0) * mat.density
        self.warnings.append(f"'{el.findtext('name')}': no mass information; assumed 0")
        return 0.0

    def _parachute(self, el: ET.Element, name: str) -> None:
        cd = el.findtext("cd") or "0.8"
        cd_v = 0.8 if cd.strip() == "auto" else float(cd)
        ev = (el.findtext("deployevent") or "apogee").strip()
        alt = _f(el, "deployaltitude")
        if ev not in ("apogee", "altitude"):
            self.warnings.append(f"'{name}': deploy event '{ev}' mapped to apogee")
            ev = "apogee"
        self.chutes.append(RecoveryDevice(name, cd=cd_v, diameter=_f(el, "diameter", 0.3),
                                          deploy_event=ev, deploy_altitude_agl=alt,
                                          delay=_f(el, "deploydelay", 0.0) or 0.0))

    def _motor_mount(self, mm: ET.Element, x: float, length: float, mount_d: float) -> None:
        overhang = _f(mm, "overhang", 0.0) or 0.0
        motors = mm.findall("motor")
        mlen = None
        for m in motors:
            ref = MotorRef(manufacturer=(m.findtext("manufacturer") or "").strip(),
                           designation=(m.findtext("designation") or "").strip(),
                           diameter_m=_f(m, "diameter"), length_m=_f(m, "length"),
                           delay=(m.findtext("delay") or None), config_id=m.get("configid"))
            self.motors.append(ref)
            mlen = mlen or ref.length_m
        mlen = mlen or length
        mdia = next((m.diameter_m for m in self.motors if m.diameter_m), None) or mount_d
        self.v.add(MotorSlot("motor", x=x + length + overhang - mlen, motor_length=mlen,
                             motor_diameter=mdia))

    def _resolve_auto(self) -> None:
        radii = [r for _, _, r in self.stack]
        known = [r for r in radii if r == r]
        default = max(known) if known else 0.0
        if not known and self.pending_auto:
            self.warnings.append("all radii are 'auto'; could not resolve - vehicle unusable")
        pending, self.pending_auto = self.pending_auto, []
        for comp, attr in pending:
            # nearest known radius along the stack (prefer aft neighbour for nose, fore for tubes)
            best = default
            if hasattr(comp, "x"):
                dists = [(abs(fx - comp.x), r) for fx, _, r in self.stack if r == r]
                dists += [(abs(ax - comp.x), r) for _, ax, r in self.stack if r == r]
                if dists:
                    best = min(dists)[1]
            setattr(comp, attr, best if attr == "body_radius" else 2 * best)


def read_ork(path: str | Path) -> OrkImport:
    root = _load_xml(path)
    if root.tag != "openrocket":
        raise ValueError("not an OpenRocket document")
    b = _Builder()
    b.build(root)
    problems = b.v.validate()
    b.warnings += [f"design check: {p}" for p in problems]
    rec = RecoveryConfig(devices=tuple(b.chutes)) if b.chutes else None
    return OrkImport(vehicle=b.v, recovery=rec, motors=b.motors, warnings=b.warnings,
                     creator=root.get("creator", ""))
