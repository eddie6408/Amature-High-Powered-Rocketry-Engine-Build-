"""STL reading and exact mass properties of closed triangle meshes.

Mass properties use the signed-tetrahedron decomposition: for each triangle
(a, b, c) the tetrahedron (0, a, b, c) has volume V = a·(b×c)/6, centroid
(a+b+c)/4 and second moment ∫x xᵀ dV = V/20 (aaᵀ + bbᵀ + ccᵀ + ssᵀ), s = a+b+c.
Summing over a closed, consistently oriented mesh gives the exact solid.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from aerodyne.core.provenance import DataKind

UNIT_SCALE = {"m": 1.0, "mm": 1e-3, "cm": 1e-2, "in": 0.0254}


def read_stl(path: str | Path) -> np.ndarray:
    """Return triangles as an (N, 3, 3) float array in file units."""
    data = Path(path).read_bytes()
    if len(data) >= 84:
        (n,) = struct.unpack_from("<I", data, 80)
        if 84 + 50 * n == len(data):
            rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)),
                                                      ("attr", "<u2")]), count=n, offset=84)
            return rec["v"].astype(float)
    text = data.decode("ascii", errors="strict")
    if not text.lstrip().lower().startswith("solid"):
        raise ValueError("not a valid STL file")
    verts = [list(map(float, line.split()[1:4])) for line in text.splitlines()
             if line.strip().lower().startswith("vertex")]
    if len(verts) % 3 or not verts:
        raise ValueError("malformed ASCII STL")
    return np.asarray(verts, dtype=float).reshape(-1, 3, 3)


def write_stl_binary(path: str | Path, tris: np.ndarray, header: bytes = b"aerodyne") -> None:
    tris = np.asarray(tris, dtype=np.float32)
    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ln = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, ln, out=np.zeros_like(normals), where=ln > 0)
    with open(path, "wb") as fh:
        fh.write(header.ljust(80, b"\0")[:80])
        fh.write(struct.pack("<I", len(tris)))
        for nrm, t in zip(normals, tris):
            fh.write(struct.pack("<12fH", *nrm, *t.ravel(), 0))


@dataclass(frozen=True)
class MeshMassProperties:
    """Unit-density properties in metres (multiply by density for mass)."""

    volume: float                  # m^3
    centroid: np.ndarray           # m, mesh frame
    inertia_per_density: np.ndarray  # 3x3 about centroid, m^5
    watertight: bool
    flipped: bool                  # mesh normals pointed inward (fixed)
    triangles: int


def _edges_watertight(tris: np.ndarray) -> bool:
    # every undirected edge must be used by exactly two triangles
    v = np.round(tris.reshape(-1, 3), 9)
    _, idx = np.unique(v, axis=0, return_inverse=True)
    f = idx.reshape(-1, 3)
    e = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
    _, counts = np.unique(e, axis=0, return_counts=True)
    return bool(np.all(counts == 2))


def mesh_mass_properties(tris: np.ndarray, units: str = "mm") -> MeshMassProperties:
    t = np.asarray(tris, dtype=float) * UNIT_SCALE[units]
    a, b, c = t[:, 0], t[:, 1], t[:, 2]
    v = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0     # signed tetra volumes
    sgn = 1.0 if v.sum() >= 0 else -1.0                     # inward normals -> flip once
    v = sgn * v
    vol = float(v.sum())
    if vol <= 0:
        raise ValueError("mesh encloses no volume")
    s = a + b + c
    centroid = (v[:, None] * s).sum(axis=0) / (4.0 * vol)
    outer = lambda u: np.einsum("ij,ik->ijk", u, u)
    C = (v[:, None, None] / 20.0 * (outer(a) + outer(b) + outer(c) + outer(s))).sum(axis=0)
    C_cm = C - vol * np.outer(centroid, centroid)
    inertia = np.trace(C_cm) * np.eye(3) - C_cm
    return MeshMassProperties(vol, centroid, inertia, _edges_watertight(t), sgn < 0, len(t))


_AXES = {"x": 0, "y": 1, "z": 2}


@dataclass(frozen=True)
class CadMassProperties:
    name: str
    mass: float
    cg_station: float          # m from nose tip (vehicle frame)
    ixx: float                 # roll, about CG
    iyy: float                 # transverse (mean of the two), about CG
    source: str
    source_sha256: str
    kind: DataKind = DataKind.ESTIMATED   # CAD geometry x nominal density
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_component(self, x: float | None = None, length: float = 0.0, tags: set[str] | None = None):
        from aerodyne.vehicle.components import CadPart

        fore = self.cg_station - length / 2 if x is None else x
        return CadPart(self.name, x=fore, cad_mass=self.mass, cad_cg=self.cg_station,
                       cad_ixx=self.ixx, cad_iyy=self.iyy, length_=length, source=self.source,
                       source_sha256=self.source_sha256, tags=tags or set())


def stl_part(path: str | Path, density: float, name: str | None = None, units: str = "mm",
             axis: str = "+z", nose_station_of_origin: float = 0.0) -> CadMassProperties:
    """Mass properties of an STL part placed in the vehicle.

    ``axis`` is the mesh axis that points *aft* along the rocket ("+z", "-x", ...);
    ``nose_station_of_origin`` is the vehicle station (m from nose tip) of the
    mesh origin.
    """
    p = Path(path)
    tris = read_stl(p)
    mp = mesh_mass_properties(tris, units)
    sign = -1.0 if axis.startswith("-") else 1.0
    k = _AXES[axis[-1]]
    others = [i for i in range(3) if i != k]
    warnings = []
    if not mp.watertight:
        warnings.append("mesh is not watertight; volume/mass may be wrong - repair in CAD")
    if mp.flipped:
        warnings.append("mesh normals were inverted; corrected")
    I = mp.inertia_per_density * density
    ixx = float(I[k, k])
    iyy_a, iyy_b = float(I[others[0], others[0]]), float(I[others[1], others[1]])
    if abs(iyy_a - iyy_b) > 0.2 * max(iyy_a, iyy_b, 1e-12):
        warnings.append("part is not axisymmetric; transverse inertia averaged")
    lat = mp.centroid[others]
    if np.linalg.norm(lat) > 0.002:
        warnings.append(f"part CG is {1000 * np.linalg.norm(lat):.1f} mm off the vehicle axis")
    return CadMassProperties(
        name=name or p.stem, mass=mp.volume * density,
        cg_station=nose_station_of_origin + sign * float(mp.centroid[k]),
        ixx=ixx, iyy=0.5 * (iyy_a + iyy_b), source=str(p),
        source_sha256=hashlib.sha256(p.read_bytes()).hexdigest(), warnings=tuple(warnings))
