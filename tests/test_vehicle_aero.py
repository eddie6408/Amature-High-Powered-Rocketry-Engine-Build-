import math

import pytest

from aerodyne.aero import (
    AnalyticalAeroModel,
    FlightCondition,
    ScaledAeroModel,
    TableAeroModel,
    barrowman_cp,
    compare_models,
)
from aerodyne.core import DataKind
from aerodyne.examples import example_motor, example_vehicle
from aerodyne.vehicle import BodyTube, FinSet, MassPropertiesEngine, NoseCone, PointMass, Vehicle


def test_tube_mass_and_inertia():
    t = BodyTube("t", x=0, length_=1.0, outer_diameter=0.1, wall_thickness=0.002,
                 material="aluminum_6061")
    expected = math.pi * (0.05 ** 2 - 0.048 ** 2) * 1.0 * 2700
    assert t.mass == pytest.approx(expected)
    assert t.cg == pytest.approx(0.5)
    ixx, iyy = t.inertia()
    assert iyy == pytest.approx(expected * (3 * (0.05 ** 2 + 0.048 ** 2) + 1) / 12)


def test_measured_mass_overrides_estimate():
    p = PointMass("sled", x=0.5, mass_estimate=0.2, mass_override=0.25)
    assert p.mass == 0.25


def test_mass_configurations_and_parallel_axis():
    v = example_vehicle()
    m = example_motor()
    e = MassPropertiesEngine(v)
    full, spent, empty = (e.configuration(c, m) for c in ("FULL", "MOTOR_SPENT", "EMPTY"))
    assert full.mass == pytest.approx(empty.mass + m.total_mass)
    assert spent.mass == pytest.approx(empty.mass + m.spent_mass)
    assert empty.cg < spent.cg < full.cg        # motor is aft
    no_payload = e.configuration("PAYLOAD_REMOVED", m)
    assert no_payload.mass == pytest.approx(full.mass - 0.06)
    assert no_payload.cg > full.cg               # nose payload removed -> CG moves aft
    travel = e.cg_travel(m, 10)
    assert travel[0][2] == pytest.approx(full.cg) and travel[-1][2] == pytest.approx(spent.cg)
    # two point masses: parallel axis check
    v2 = Vehicle("pm")
    v2.add(PointMass("a", x=0.0, mass_estimate=1.0))
    v2.add(PointMass("b", x=2.0, mass_estimate=1.0))
    mp = MassPropertiesEngine(v2).dry()
    assert mp.cg == pytest.approx(1.0) and mp.iyy == pytest.approx(2.0)


def test_barrowman_textbook_values():
    # conical nose CNa = 2 at 2/3 L; fins give the remainder of the total
    v = Vehicle("b")
    v.add(NoseCone("n", x=0, length_=0.3, diameter=0.1, shape="conical"))
    v.add(BodyTube("b", x=0.3, length_=1.0, outer_diameter=0.1))
    v.add(FinSet("f", x=1.1, count=4, root_chord=0.2, tip_chord=0.1, span=0.1, sweep=0.1,
                 body_radius=0.05))
    cna, xcp, parts = barrowman_cp(v)
    assert parts[0][1] == pytest.approx(2.0) and parts[0][2] == pytest.approx(0.2)
    # fin term by hand
    lm = math.hypot(0.1, 0.1 + 0.05 - 0.1)
    fin = (1 + 0.05 / 0.15) * (4 * 4 * 1.0) / (1 + math.sqrt(1 + (2 * lm / 0.3) ** 2))
    assert parts[1][1] == pytest.approx(fin)
    assert 0.3 < xcp < 1.3


def test_analytical_drag_is_plausible_and_transonic_rise():
    a = AnalyticalAeroModel(example_vehicle())
    cd_sub = a.zero_lift_cd(0.3, 5e6)
    cd_trans = a.zero_lift_cd(1.05, 5e6)
    assert 0.3 < cd_sub < 0.8
    assert cd_trans > cd_sub
    c = a.coefficients(FlightCondition(0.3, 5e6, alpha=math.radians(4)))
    assert c.kind == DataKind.ESTIMATED and c.cn > 0 and c.cd > c.ca


def test_table_model_and_comparison(tmp_path):
    v = example_vehicle()
    base = AnalyticalAeroModel(v)
    p = tmp_path / "rasaero.csv"
    p.write_text("Mach,CD Power-Off\n0.1,0.50\n0.5,0.52\n1.0,0.80\n2.0,0.55\n")
    tab = TableAeroModel.from_csv(p, v.reference_diameter, source="RASAero II export",
                                  fallback=base, columns={"mach": "Mach", "cd": "CD Power-Off"})
    c = tab.coefficients(FlightCondition(0.3, 5e6))
    assert c.cd == pytest.approx(0.51)
    assert c.kind == DataKind.ESTIMATED       # CP came from the analytical fallback
    rep = compare_models({"aerodyne": base, "rasaero": tab}, [0.2, 0.5, 0.9])
    assert rep["max_cd_spread_rel"] >= 0 and len(rep["rows"]) == 3
    scaled = ScaledAeroModel(base, cd_scale=1.2)
    assert scaled.coefficients(FlightCondition(0.3, 5e6)).kind == DataKind.HYPOTHETICAL


def test_vehicle_roundtrip_and_validation():
    v = example_vehicle()
    v2 = Vehicle.from_dict(v.to_dict())
    assert v2.config_hash == v.config_hash
    assert v.validate() == []
    assert "no fin set" in " ".join(Vehicle("x").validate())
