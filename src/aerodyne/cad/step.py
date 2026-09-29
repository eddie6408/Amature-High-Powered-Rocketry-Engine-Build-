"""STEP import (exact B-rep mass properties via OpenCASCADE, through gmsh).

Optional dependency: ``pip install gmsh`` (on headless Linux also the GL runtime,
e.g. ``apt install libglu1-mesa``). Each solid body in the file becomes one part;
volume, centroid and inertia come from the exact CAD geometry, not a mesh.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from aerodyne.cad.stl import _AXES, CadMassProperties


def available() -> bool:
    try:
        import gmsh
    except (ImportError, OSError):          # OSError: gmsh present but its GL runtime is missing
        return False
    return hasattr(gmsh, "initialize")


def step_parts(path: str | Path, density: float, axis: str = "+z", nose_station_of_origin: float = 0.0,
               name: str | None = None, merge: bool = False) -> list[CadMassProperties]:
    """Mass properties of every solid in a STEP file, placed in the vehicle frame.
    ``axis`` is the model axis pointing aft; ``merge`` combines all solids into one part."""
    try:
        import gmsh
    except (ImportError, OSError) as exc:
        raise RuntimeError("STEP import needs gmsh: pip install gmsh (and libglu1-mesa on Linux)") from exc
    p = Path(path)
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    # gmsh/OpenCASCADE install their own signal handlers (SIGPIPE back to "terminate");
    # keep the host process's handlers - a server must survive a client disconnecting
    import signal

    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGPIPE, signal.SIGINT)}
    gmsh.initialize(["-noenv"], interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setString("Geometry.OCCTargetUnit", "M")      # SI regardless of file units
        gmsh.model.add("aerodyne")
        gmsh.model.occ.importShapes(str(p))
        gmsh.model.occ.synchronize()
        vols = gmsh.model.getEntities(3)
        if not vols:
            raise ValueError("STEP file contains no solid bodies")
        bodies = []
        for dim, tag in vols:
            v = gmsh.model.occ.getMass(dim, tag)
            c = np.array(gmsh.model.occ.getCenterOfMass(dim, tag))
            inertia = np.array(gmsh.model.occ.getMatrixOfInertia(dim, tag)).reshape(3, 3)
            raw = gmsh.model.getEntityName(dim, tag)
            # translator-generated names ("Shapes/Open CASCADE STEP translator ...") are noise
            label = raw.split("/")[-1] if raw and "translator" not in raw else f"{name or p.stem} body {tag}"
            bodies.append((label, abs(v), c, inertia))
    finally:
        gmsh.finalize()
        for sig, handler in saved.items():
            signal.signal(sig, handler)
    if merge:
        vt = sum(b[1] for b in bodies)
        c = sum(b[1] * b[2] for b in bodies) / vt
        inertia = sum(b[3] + b[1] * ((b[2] - c) @ (b[2] - c) * np.eye(3) - np.outer(b[2] - c, b[2] - c))
                      for b in bodies)
        bodies = [(name or p.stem, vt, c, inertia)]
    sign = -1.0 if axis.startswith("-") else 1.0
    k = _AXES[axis[-1]]
    others = [i for i in range(3) if i != k]
    out = []
    for label, vol, c, inertia in bodies:
        I = inertia * density
        iy = [float(I[o, o]) for o in others]
        warns = []
        if abs(iy[0] - iy[1]) > 0.2 * max(max(iy), 1e-12):
            warns.append("part is not axisymmetric; transverse inertia averaged")
        if np.linalg.norm(c[others]) > 0.002:
            warns.append(f"part CG is {1000 * np.linalg.norm(c[others]):.1f} mm off the vehicle axis")
        out.append(CadMassProperties(name=label, mass=vol * density, cg_station=nose_station_of_origin + sign * float(c[k]),
                                     ixx=float(I[k, k]), iyy=0.5 * (iy[0] + iy[1]), source=str(p),
                                     source_sha256=sha, warnings=tuple(warns)))
    return out
