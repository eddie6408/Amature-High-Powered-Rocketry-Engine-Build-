"""Import mass-property reports exported from CAD tools.

Expected CSV columns (case-insensitive; units in the header or via arguments):

    name, mass_kg, cg_m, ixx_kgm2, iyy_kgm2 [, length_m, tags]

``cg_m`` is the vehicle station (m from nose tip). Fusion 360 / SolidWorks
report inertia about the part CG in their own axes - map the roll axis to
ixx and a transverse axis to iyy when exporting. CAD-derived values are
ESTIMATED (nominal density); weigh parts and use ``mass_override`` to replace
them with MEASURED values.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from aerodyne.cad.stl import CadMassProperties

_ALIASES = {
    "name": ("name", "component", "part"),
    "mass": ("mass_kg", "mass", "mass (kg)"),
    "cg": ("cg_m", "cg", "cg station (m)", "x_cg_m"),
    "ixx": ("ixx_kgm2", "ixx", "roll inertia (kg m^2)"),
    "iyy": ("iyy_kgm2", "iyy", "pitch inertia (kg m^2)"),
    "length": ("length_m", "length"),
    "tags": ("tags",),
}


def read_mass_properties_csv(path: str | Path, mass_scale: float = 1.0, length_scale: float = 1.0,
                             inertia_scale: float = 1.0) -> list[CadMassProperties]:
    """``*_scale`` convert file units to SI (e.g. mass_scale=1e-3 for grams)."""
    p = Path(path)
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    with open(p, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return []
    header = {h.lower().strip(): h for h in rows[0]}

    def col(key: str, required: bool = True) -> str | None:
        for alias in _ALIASES[key]:
            if alias in header:
                return header[alias]
        if required:
            raise ValueError(f"mass-property CSV lacks a {key} column ({_ALIASES[key]})")
        return None

    c = {k: col(k, k in ("name", "mass", "cg")) for k in _ALIASES}
    out = []
    for r in rows:
        tags = tuple(t.strip() for t in (r.get(c["tags"]) or "").split(";") if t.strip()) if c["tags"] else ()
        out.append(CadMassProperties(
            name=r[c["name"]], mass=float(r[c["mass"]]) * mass_scale,
            cg_station=float(r[c["cg"]]) * length_scale,
            ixx=float(r[c["ixx"]]) * inertia_scale if c["ixx"] and r[c["ixx"]] else 0.0,
            iyy=float(r[c["iyy"]]) * inertia_scale if c["iyy"] and r[c["iyy"]] else 0.0,
            source=f"{p}#{r[c['name']]}", source_sha256=sha,
            warnings=tags))
    return out
