import numpy as np
import pytest

from aerodyne.core import DataKind, DataQuality
from aerodyne.propulsion import (
    MotorDatabase,
    MotorMetadata,
    MotorPerformance,
    analyze_thrust_data,
    impulse_class,
    read_eng,
    write_eng,
)
from aerodyne.propulsion.motor import synthetic_motor


def test_impulse_classes():
    assert impulse_class(2.5) == "A"
    assert impulse_class(2.6) == "B"
    assert impulse_class(160.0) == "G"
    assert impulse_class(300.0) == "H"
    assert impulse_class(640.0) == "I"
    assert impulse_class(1.0) == "1/2A"


def test_motor_performance_properties():
    meta = MotorMetadata("X", "T-1", "unit test", "2026-01-01", DataQuality.MANUFACTURER)
    m = MotorPerformance(np.array([0.0, 1.0, 2.0]), np.array([0.0, 100.0, 0.0]), 0.5, 0.2, meta)
    assert m.total_impulse == pytest.approx(100.0)
    assert m.burn_time == pytest.approx(2.0)
    assert m.average_thrust == pytest.approx(50.0)
    assert m.mass_at(0) == pytest.approx(0.5)
    assert m.mass_at(1.0) == pytest.approx(0.4)   # half the impulse delivered
    assert m.mass_at(5) == pytest.approx(0.3)
    assert m.thrust_at(0.5) == pytest.approx(50.0)
    with pytest.raises(ValueError):
        MotorPerformance(np.array([0.0, 1.0]), np.array([0.0, -1.0]), 0.5, 0.2, meta)


def test_scaled_motor_is_labelled_hypothetical():
    m = synthetic_motor()
    s = m.scaled(1.1, 1.0)
    assert s.total_impulse == pytest.approx(1.1 * m.total_impulse, rel=1e-6)
    assert s.metadata.data_quality == DataQuality.HYPOTHETICAL


def test_eng_roundtrip(tmp_path):
    m = synthetic_motor()
    p = tmp_path / "m.eng"
    write_eng(m, p)
    (back,) = read_eng(p, data_quality=DataQuality.MANUFACTURER)
    assert back.total_impulse == pytest.approx(m.total_impulse, rel=1e-3)
    assert back.metadata.data_quality == DataQuality.MANUFACTURER
    assert read_eng(p)[0].metadata.data_quality == DataQuality.UNKNOWN


def test_database_never_substitutes_quality(tmp_path):
    db = MotorDatabase()
    cert = synthetic_motor("H-TEST")
    cert = MotorPerformance(cert.time, cert.thrust, cert.total_mass, cert.propellant_mass,
                            MotorMetadata("Acme", "H-TEST", "cert db", "2025-05-01",
                                          DataQuality.CERTIFIED))
    est = MotorPerformance(cert.time, cert.thrust * 1.05, cert.total_mass, cert.propellant_mass,
                           MotorMetadata("Acme", "H-TEST", "sim guess", "2025-05-01",
                                         DataQuality.ESTIMATED))
    db.add(est)
    db.add(cert)
    assert [m.metadata.data_quality for _, m in db.candidates("h-test")] == [
        DataQuality.CERTIFIED, DataQuality.ESTIMATED]
    with pytest.raises(LookupError):
        db.get("H-TEST", accept=[DataQuality.MEASURED])
    assert db.get("H-TEST", accept=[DataQuality.ESTIMATED]).metadata.source == "sim guess"
    with pytest.raises(ValueError):
        db.add(cert)
    db.save(tmp_path / "db.json")
    assert len(MotorDatabase.load(tmp_path / "db.json")) == 2
    assert [m.metadata.designation for m in db.search(impulse_letter="H")] == ["H-TEST", "H-TEST"]


def test_thrust_analyzer_recovers_known_curve():
    rng = np.random.default_rng(0)
    ref = synthetic_motor()
    fs = 1000.0
    t = np.arange(-0.5, 2.5, 1 / fs)
    f = np.array([ref.thrust_at(x) for x in t]) + 3.0 + rng.normal(0, 0.8, len(t))  # 3 N tare
    res = analyze_thrust_data(t, f, calibration_uncertainty_rel=0.01)
    assert res.kind == DataKind.DERIVED
    assert res.baseline == pytest.approx(3.0, abs=0.2)
    assert res.total_impulse == pytest.approx(ref.total_impulse, rel=0.01)
    assert abs(res.total_impulse - ref.total_impulse) < 3 * res.total_impulse_sigma
    assert res.peak_thrust == pytest.approx(ref.peak_thrust, rel=0.03)
    assert res.burn_time == pytest.approx(ref.burn_time, rel=0.1)
    motor = res.to_motor("Acme", "H-TEST", 0.35, 0.18, "stand-1", "2026-06-01")
    assert motor.metadata.data_quality == DataQuality.MEASURED
    assert np.array_equal(res.raw_force, f)    # raw data untouched


def test_thrust_analyzer_rejects_bad_input():
    with pytest.raises(ValueError):
        analyze_thrust_data([0, 1, 2], [0, 1, 0])
    t = np.linspace(0, 1, 50)
    f = np.ones(50)
    f[10] = np.nan
    with pytest.raises(ValueError):
        analyze_thrust_data(t, f)
