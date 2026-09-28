from dataclasses import replace

import pytest

from aerodyne.aero.model import ScaledAeroModel
from aerodyne.analysis import comparison, model_error
from aerodyne.analysis.ingestion import load_logger_export, logger_to_dataset
from aerodyne.analysis.reconstruction import FlightReconstructionEngine
from aerodyne.analysis.test_data import ChannelSpec, analyze_test_data
from aerodyne.avionics.telemetry import TelemetryPacket
from aerodyne.core import DataKind
from aerodyne.dynamics.simulator import FlightSimulator
from aerodyne.examples import example_config, example_motor, example_recovery, example_vehicle
from aerodyne.ground import GroundStation
from aerodyne.sil import cbackend
from aerodyne.sil.faults import STANDARD_SCENARIOS
from aerodyne.sil.hil import encode_injection
from aerodyne.sil.runner import SILRunner
from aerodyne.twin import DigitalTwin

needs_c = pytest.mark.skipif(not cbackend.available(),
                             reason="C flight software not built (cmake -S firmware -B firmware/build)")


@pytest.mark.parametrize("scenario", sorted(STANDARD_SCENARIOS))
def test_fault_scenarios_python(nominal_sim, scenario):
    r = SILRunner(nominal_sim, STANDARD_SCENARIOS[scenario]).run()
    assert r.evaluate() == [], scenario
    assert r.final_state == "LANDED"


def test_faults_are_detected(nominal_sim):
    expect = {"imu_failure_coast": "sensor:imu_accel", "baro_failure_boost": "sensor:baro",
              "gnss_loss": "sensor:gnss", "low_battery": "low_battery",
              "storage_failure": "storage", "baro_spike_pad": "sensor:baro"}
    for name, fault in expect.items():
        r = SILRunner(nominal_sim, STANDARD_SCENARIOS[name]).run()
        assert fault in r.faults_detected, name
    assert SILRunner(nominal_sim, []).run().faults_detected == []
    assert SILRunner(nominal_sim, STANDARD_SCENARIOS["processor_reset_coast"]).run().boots == 2
    assert SILRunner(nominal_sim, STANDARD_SCENARIOS["corrupted_packets"]).run().link["crc_failures"] > 0


@needs_c
@pytest.mark.parametrize("scenario", sorted(STANDARD_SCENARIOS))
def test_c_flight_software_matches_reference(nominal_sim, scenario):
    rp = SILRunner(nominal_sim, STANDARD_SCENARIOS[scenario], backend="python").run()
    rc = SILRunner(nominal_sim, STANDARD_SCENARIOS[scenario], backend="c").run()
    assert [(t, s) for t, _, s, _ in rc.transitions] == [(t, s) for t, _, s, _ in rp.transitions]
    assert rc.evaluate() == []


@needs_c
@pytest.mark.parametrize("scenario", sorted(STANDARD_SCENARIOS))
def test_full_c_application(nominal_sim, scenario):
    """The complete C flight application (firmware/src/aero_app.c) flies every scenario."""
    r = SILRunner(nominal_sim, STANDARD_SCENARIOS[scenario], backend="c-app").run()
    assert r.evaluate() == [], scenario
    assert r.final_state == "LANDED"
    if scenario == "processor_reset_coast":
        assert r.boots == 2
    expected_fault = {"imu_failure_coast": "sensor:imu_accel", "baro_failure_boost": "sensor:baro",
                      "gnss_loss": "sensor:gnss", "low_battery": "low_battery",
                      "storage_failure": "storage"}.get(scenario)
    if expected_fault:
        assert expected_fault in r.faults_detected
    if scenario == "nominal":
        assert r.faults_detected == []
        assert r.est_apogee == pytest.approx(r.true_apogee, rel=0.02)


@needs_c
def test_c_telemetry_bytes_identical():
    p = TelemetryPacket(7, 3, 123456, 98765, 1234.5, -12.25, 9.5, 35.1234567, -117.7654321,
                        (0.7071, 0.0, 0.7071, 0.0), 8123, -4.5, 4, 0xBEEF, 7, 3, 11)
    assert cbackend.c_encode(p) == p.encode()


def test_closed_loop_reconstruction_and_diagnosis(tmp_path):
    base = example_config()
    predicted = FlightSimulator(base).run().summary()
    truth_sim = FlightSimulator(replace(base, aero=ScaledAeroModel(base.aero, cd_scale=1.15))).run()
    sil = SILRunner(truth_sim, []).run()
    # through the file export path, as after a real flight
    sil.logger.export(tmp_path)
    ds = load_logger_export(tmp_path, "SYN")
    rec = FlightReconstructionEngine(ds).run()
    actual = rec.summary()
    truth = truth_sim.summary()
    assert actual["apogee_agl_m"] == pytest.approx(truth["apogee_agl_m"], rel=0.02)
    assert actual["time_to_apogee_s"] == pytest.approx(truth["time_to_apogee_s"], abs=0.3)
    assert actual["landing_east_m"] == pytest.approx(truth["landing_east_m"], abs=15)
    rows = {r.key: r for r in comparison.compare(predicted, actual)}
    assert rows["apogee_agl_m"].pct_error > 3            # model over-predicts
    names = [c.name for c in model_error.diagnose(predicted, actual)]
    assert "drag-model error" in names
    assert "motor-performance difference" not in names   # boost matched
    # synthetic data stays labelled as such
    ds2 = logger_to_dataset(sil.logger, "SYN", kind=DataKind.HYPOTHETICAL)
    assert FlightReconstructionEngine(ds2).run().kind == DataKind.HYPOTHETICAL


def test_digital_twin_validation_and_calibration():
    twin = DigitalTwin("AERODYNE-EX1", "REV-A", example_vehicle(), example_motor(), example_recovery())
    st = twin.stability()
    assert st["margin_full_cal"] > 1.0
    assert len(twin.config_hash) == 64
    base = example_config()
    pred = FlightSimulator(base).run().summary()
    assert twin.validate_against_flight("F1", pred, pred).status == "VALIDATED"
    worse = dict(pred, apogee_agl_m=pred["apogee_agl_m"] * 0.9)
    assert twin.validate_against_flight("F2", pred, worse).status == "DEVIATION"
    assert len(twin.validations) == 2
    target = FlightSimulator(replace(base, aero=ScaledAeroModel(base.aero, cd_scale=1.2),
                                     stop_at_apogee=True, dt=0.01)).run().summary()["apogee_agl_m"]
    prop = twin.propose_drag_calibration(target, base)
    assert prop.value == pytest.approx(1.2, abs=0.02) and prop.kind == DataKind.ESTIMATED
    with pytest.raises(ValueError):
        twin.propose_drag_calibration(50.0, base)


def test_ground_station(nominal_sim):
    r = SILRunner(nominal_sim, []).run()
    gs = GroundStation(vehicle_id=1)
    for i, p in enumerate(r.packets_received):
        gs.feed(p.encode(), now=i * 0.1)
        if gs.status.state == "DESCENT" and gs.landing_estimate():
            est = gs.landing_estimate()
            assert est["radius_m"] > 0 and est["kind"] == "ESTIMATED"
    assert gs.status.state == "LANDED"
    assert gs.status.max_altitude_m == pytest.approx(r.true_apogee, rel=0.03)
    txt = gs.render()
    assert "AERODYNE GROUND" in txt and "LANDED" in txt


def test_generic_test_data():
    import numpy as np

    t = np.linspace(0, 2, 2001)
    rng = np.random.default_rng(0)
    force = np.where((t > 0.5) & (t < 1.5), 100.0, 0.0) + rng.normal(0, 1, t.size)
    temp = 20 + 5 * t + rng.normal(0, 0.1, t.size)
    res = analyze_test_data(t, {"force": force, "temperature": temp},
                            {"force": ChannelSpec("force", "N", 0.01, cutoff_hz=50)}, "T-1",
                            quiet_until=0.4)
    rep = res.report()
    assert rep["channels"]["force"]["noise_sigma"] == pytest.approx(1.0, rel=0.2)
    assert res.channels["force"].kind_raw == DataKind.MEASURED
    assert np.std(res.channels["force"].filtered[:300]) < np.std(force[:300])


def test_hil_injection_frame():
    from aerodyne.avionics.sensors import SensorReading

    f = encode_injection(SensorReading("baro", "baro", 123.4, 1.5, 7))
    assert f[:2] == b"\xa5\x5a" and len(f) > 10
