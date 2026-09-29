import math
import zipfile
from pathlib import Path

import numpy as np
import pytest

from aerodyne.cad import CadPart, mesh_mass_properties, read_mass_properties_csv, read_stl
from aerodyne.cad.stl import stl_part, write_stl_binary
from aerodyne.core import DataKind
from aerodyne.dynamics.simulator import FlightSimulator, SimulationConfig
from aerodyne.aero import AnalyticalAeroModel
from aerodyne.interop import read_ork
from aerodyne.propulsion.motor import synthetic_motor
from aerodyne.vehicle import MassPropertiesEngine, Vehicle

FIXTURE = Path(__file__).parent / "data" / "dual_deploy.ork.xml"


def _box(lx, ly, lz):
    V = np.array([[0, 0, 0], [lx, 0, 0], [lx, ly, 0], [0, ly, 0],
                  [0, 0, lz], [lx, 0, lz], [lx, ly, lz], [0, ly, lz]], float)
    F = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
         [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    return V[F]


def _tube(ro, ri, length, n=128):
    """Closed annular cylinder along +z, outward normals."""
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    tris = []
    for i in range(n):
        a, b = th[i], th[(i + 1) % n]
        o0, o1 = [ro * np.cos(a), ro * np.sin(a)], [ro * np.cos(b), ro * np.sin(b)]
        i0, i1 = [ri * np.cos(a), ri * np.sin(a)], [ri * np.cos(b), ri * np.sin(b)]
        tris += [[[*o0, 0], [*o1, 0], [*o1, length]], [[*o0, 0], [*o1, length], [*o0, length]]]
        tris += [[[*i0, 0], [*i1, length], [*i1, 0]], [[*i0, 0], [*i0, length], [*i1, length]]]
        tris += [[[*o0, 0], [*i1, 0], [*o1, 0]], [[*o0, 0], [*i0, 0], [*i1, 0]]]
        tris += [[[*o0, length], [*o1, length], [*i1, length]], [[*o0, length], [*i1, length], [*i0, length]]]
    return np.array(tris, float)


def test_box_mass_properties_exact():
    m = mesh_mass_properties(_box(2, 3, 4), "m")
    assert m.volume == pytest.approx(24)
    assert np.allclose(m.centroid, [1, 1.5, 2])
    assert np.allclose(np.diag(m.inertia_per_density), 24 * np.array([9 + 16, 4 + 16, 4 + 9]) / 12)
    assert m.watertight and not m.flipped
    flipped = mesh_mass_properties(_box(2, 3, 4)[:, ::-1], "m")
    assert flipped.flipped and flipped.volume == pytest.approx(24)
    open_mesh = mesh_mass_properties(_box(2, 3, 4)[:-1], "m")
    assert not open_mesh.watertight


def test_stl_binary_and_ascii_roundtrip(tmp_path):
    tris = _box(10, 20, 30)
    write_stl_binary(tmp_path / "b.stl", tris)
    assert np.allclose(read_stl(tmp_path / "b.stl"), tris)
    lines = ["solid box"]
    for t in tris:
        lines += ["facet normal 0 0 0", "outer loop", *[f"vertex {v[0]} {v[1]} {v[2]}" for v in t],
                  "endloop", "endfacet"]
    (tmp_path / "a.stl").write_text("\n".join(lines + ["endsolid box"]))
    assert np.allclose(read_stl(tmp_path / "a.stl"), tris)


def test_tube_part_matches_analytic_and_places_on_vehicle(tmp_path):
    ro, ri, L, rho = 33.0, 31.5, 400.0, 1850.0          # mm, fiberglass
    write_stl_binary(tmp_path / "tube.stl", _tube(ro, ri, L))
    part = stl_part(tmp_path / "tube.stl", rho, units="mm", axis="+z", nose_station_of_origin=0.3)
    mass_exact = math.pi * ((ro / 1e3) ** 2 - (ri / 1e3) ** 2) * (L / 1e3) * rho
    assert part.mass == pytest.approx(mass_exact, rel=0.002)   # 128-gon vs circle
    assert part.cg_station == pytest.approx(0.3 + 0.2, abs=1e-9)
    iyy_exact = mass_exact * (3 * ((ro / 1e3) ** 2 + (ri / 1e3) ** 2) + (L / 1e3) ** 2) / 12
    assert part.iyy == pytest.approx(iyy_exact, rel=0.003)
    assert part.kind == DataKind.ESTIMATED and len(part.source_sha256) == 64
    assert part.warnings == ()
    comp = part.to_component(length=0.4)
    v = Vehicle("cad")
    v.add(comp)
    mp = MassPropertiesEngine(v).dry()
    assert mp.cg == pytest.approx(0.5) and mp.iyy == pytest.approx(part.iyy)
    assert Vehicle.from_dict(v.to_dict()).config_hash == v.config_hash
    # reversed axis: mesh +z points toward the nose
    rev = stl_part(tmp_path / "tube.stl", rho, axis="-z", nose_station_of_origin=0.7)
    assert rev.cg_station == pytest.approx(0.5)


def test_mass_property_csv(tmp_path):
    p = tmp_path / "props.csv"
    p.write_text("Name,Mass (kg),CG station (m),Ixx,Iyy,Tags\n"
                 "Avionics sled,0.21,0.66,0.0001,0.0005,avionics\n"
                 "Ballast,0.05,0.2,,,payload\n")
    parts = read_mass_properties_csv(p)
    assert [x.name for x in parts] == ["Avionics sled", "Ballast"]
    assert parts[0].mass == 0.21 and parts[0].iyy == 0.0005 and parts[1].ixx == 0.0
    assert isinstance(parts[0].to_component(length=0.1), CadPart)


def test_openrocket_import(tmp_path):
    ork = tmp_path / "rocket.ork"
    with zipfile.ZipFile(ork, "w") as z:
        z.write(FIXTURE, "rocket.ork")
    for path in (ork, FIXTURE):   # zipped and plain XML
        r = read_ork(path)
        comps = {c.name: c for c in r.vehicle.components}
        assert r.vehicle.name == "Fixture 54" and r.creator.startswith("OpenRocket")
        assert comps["Upper tube"].x == pytest.approx(0.25)
        assert comps["Lower tube"].x == pytest.approx(0.65)
        assert comps["Lower tube"].outer_diameter == pytest.approx(0.056)   # 'auto' radius
        assert comps["Nose cone"].diameter == pytest.approx(0.056)
        assert comps["Fins"].x == pytest.approx(1.06) and comps["Fins"].body_radius == pytest.approx(0.028)
        assert comps["GPS tracker"].x == pytest.approx(0.12)
        assert comps["Forward ring"].mass > 0
        assert comps["Lower tube"].mass == pytest.approx(0.18)       # override
        assert r.vehicle.motor_slot.x == pytest.approx(1.2 + 0.005 - 0.24)
        assert [(m.manufacturer, m.designation) for m in r.motors] == [("ExampleCo", "H200")]
        dev = {d.name: d for d in r.recovery.devices}
        assert dev["Main"].deploy_event == "altitude" and dev["Main"].deploy_altitude_agl == 150
        assert dev["Drogue"].delay == 1.0
        assert any("freeform fin without points; skipped" in w for w in r.warnings)
        assert any("MEASURED" in w for w in r.warnings)
        assert r.vehicle.validate() == []
    # the imported design flies in the 6-DOF simulator
    res = FlightSimulator(SimulationConfig(vehicle=r.vehicle, motor=synthetic_motor(),
                                           aero=AnalyticalAeroModel(r.vehicle),
                                           recovery=r.recovery)).run()
    assert res.summary()["apogee_agl_m"] > 300 and res.phase[-1] == "landed"


def test_openrocket_rejects_other_xml(tmp_path):
    p = tmp_path / "x.ork"
    p.write_text("<notarocket/>")
    with pytest.raises(ValueError):
        read_ork(p)
