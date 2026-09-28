import math

import numpy as np
import pytest

from aerodyne.avionics.estimation import AltitudeKalmanFilter
from aerodyne.avionics.firmware import FirmwareIdentity, sha256_bytes
from aerodyne.avionics.logger import FlightDataLogger, StorageError
from aerodyne.avionics.preflight import (
    FlightComputerStatus,
    PreflightRequirements,
    run_preflight,
)
from aerodyne.avionics.sensors import (
    DEFAULT_LIMITS,
    Health,
    SensorReading,
    SensorValidator,
    Status,
)
from aerodyne.avionics.state_machine import FlightState, FlightStateMachine, FsmInputs
from aerodyne.avionics.telemetry import (
    FRAME_SIZE,
    TelemetryPacket,
    TelemetryReceiver,
    crc16_ccitt,
    pack_sensor_status,
    unpack_sensor_status,
)


def _pkt(seq, **kw):
    base = dict(vehicle_id=1, flight_id=2, sequence=seq, timestamp_ms=seq * 100, altitude=100.5,
                velocity=-3.25, acceleration=9.8, latitude=35.1234567, longitude=-117.7654321,
                attitude=(1.0, 0.0, 0.0, 0.0), battery_mv=8100, temperature_c=21.3,
                system_status=4, sensor_status=0, nav_status=3, gnss_fix=3, gnss_sats=10)
    base.update(kw)
    return TelemetryPacket(**base)


def test_crc_check_value():
    assert crc16_ccitt(b"123456789") == 0x29B1


def test_packet_roundtrip():
    p = _pkt(42)
    frame = p.encode()
    assert len(frame) == FRAME_SIZE
    q = TelemetryPacket.decode(frame)
    assert q.sequence == 42 and q.latitude == pytest.approx(35.1234567, abs=1e-7)
    assert q.temperature_c == pytest.approx(21.3)
    bad = bytearray(frame)
    bad[20] ^= 0x10
    with pytest.raises(ValueError):
        TelemetryPacket.decode(bytes(bad))


def test_receiver_handles_loss_dup_reorder_corruption():
    rx = TelemetryReceiver(vehicle_id=1)
    frames = {i: _pkt(i).encode() for i in range(1, 11)}
    stream = [frames[1], frames[2], frames[2], frames[4], frames[3], frames[6]]
    corrupt = bytearray(frames[7])
    corrupt[30] ^= 0xFF
    stream += [b"\x00garbage\xae", bytes(corrupt), frames[8]]
    got = []
    for i, chunk in enumerate(stream):
        got += rx.feed(chunk, rx_time=i * 0.1)
    seqs = [p.sequence for p, _ in got]
    assert seqs == [1, 2, 4, 3, 6, 8]
    assert [ok for _, ok in got] == [True, True, True, False, True, True]
    st = rx.stats
    assert st.duplicates == 1 and st.out_of_order == 1 and st.crc_failures >= 1
    assert st.lost == 2            # 5 and 7 never arrived (3 arrived late)
    assert rx.check_link(0.8) and not rx.check_link(10.0)


def test_receiver_split_frames_and_link_interruption():
    rx = TelemetryReceiver()
    f = _pkt(1).encode() + _pkt(2).encode()
    assert rx.feed(f[:10], 0.0) == []
    assert len(rx.feed(f[10:70], 0.1)) == 1
    assert len(rx.feed(f[70:], 0.2)) == 1
    rx.feed(_pkt(3).encode(), 5.0)
    assert rx.stats.link_interruptions == 1


def test_sensor_status_bits():
    word = pack_sensor_status({"imu_accel": 0, "baro": 2, "gnss": 1})
    back = unpack_sensor_status(word)
    assert back["baro"] == 2 and back["gnss"] == 1 and back["imu_accel"] == 0


def _r(v, t, status=Status.OK, kind="baro"):
    return SensorReading(kind, kind, v, t, int(t * 100), status)


def test_validator_detects_each_failure_class():
    v = SensorValidator(DEFAULT_LIMITS["baro"])
    for i in range(20):
        assert v.check(_r(100.0 + 0.1 * (i % 3), i * 0.02), i * 0.02).ok
    assert v.health == Health.OK
    assert "range" in v.check(_r(99999.0, 0.42), 0.42).failures
    assert v.health == Health.DEGRADED
    assert "timestamp" in v.check(_r(100.0, 0.10), 0.44).failures
    assert "missing" in v.check(_r(float("nan"), 0.46), 0.46).failures
    assert "status:COMM_ERROR" in v.check(_r(100.0, 0.48, Status.COMM_ERROR), 0.48).failures
    assert "rate" in v.check(_r(160.0, 0.50), 0.50).failures
    for i in range(12):
        v.check(_r(100.0, 0.52 + i * 0.02), 0.52 + i * 0.02)
    assert v.health == Health.OK     # recovers after consecutive good samples
    outl = SensorValidator(DEFAULT_LIMITS["baro"])
    for i in range(20):
        outl.check(_r(100.0 + 0.1 * (i % 3), i * 0.02), i * 0.02)
    assert "outlier" in outl.check(_r(115.0 + 20, 0.42), 0.42).failures


def test_validator_marks_failed_after_persistent_errors():
    v = SensorValidator(DEFAULT_LIMITS["imu_accel"])
    for i in range(30):
        v.check(_r((math.nan,) * 3, i * 0.01, Status.COMM_ERROR, "imu_accel"), i * 0.01)
    assert v.health == Health.FAILED


def test_kalman_tracks_climb():
    kf = AltitudeKalmanFilter()
    h = vel = 0.0
    rng = np.random.default_rng(1)
    for _ in range(500):
        vel += 20 * 0.01
        h += vel * 0.01
        kf.predict(20.0 + rng.normal(0, 1), 0.01)
        kf.update(h + rng.normal(0, 1))
    assert kf.altitude == pytest.approx(h, abs=2)
    assert kf.velocity == pytest.approx(vel, abs=2)


def _inp(t, **kw):
    d = dict(t=t, accel_axial=9.8, accel_ok=True, baro_alt_agl=0.0, baro_ok=True, est_alt_agl=0.0,
             est_vel=0.0)
    d.update(kw)
    return FsmInputs(**d)


def _armed():
    f = FlightStateMachine()
    f.step(_inp(0, command="preflight"))
    f.step(_inp(0.01, command="arm", preflight_ok=False))
    assert f.state == FlightState.PREFLIGHT
    f.step(_inp(0.02, command="arm", preflight_ok=True))
    assert f.state == FlightState.ARMED
    return f


def test_fsm_single_sample_never_transitions():
    f = _armed()
    f.step(_inp(1.0, accel_axial=400.0))
    f.step(_inp(1.01, baro_alt_agl=900.0))
    for i in range(200):
        f.step(_inp(1.02 + i * 0.01))
    assert f.state == FlightState.ARMED


def test_fsm_apogee_requires_both_sources_when_healthy():
    f = FlightStateMachine()
    f.restore(FlightState.COAST, t_liftoff=0.0, max_baro_alt=500.0)
    # velocity says falling but baro keeps rising -> no apogee
    for i in range(100):
        f.step(_inp(5 + i * 0.01, accel_axial=-5, est_vel=-1.0, baro_alt_agl=500 + i))
    assert f.state == FlightState.COAST
    # both agree -> apogee
    for i in range(50):
        f.step(_inp(7 + i * 0.01, accel_axial=-5, est_vel=-2.0, baro_alt_agl=400.0))
    assert f.state == FlightState.DESCENT
    assert "velocity<0 and baro drop" in f.transitions[-1].reason


def test_fsm_apogee_lockout():
    f = FlightStateMachine()
    f.restore(FlightState.COAST, t_liftoff=0.0, max_baro_alt=500.0)
    for i in range(100):
        f.step(_inp(1 + i * 0.01, est_vel=-5, baro_alt_agl=100))
    assert f.state == FlightState.COAST    # before 3 s lockout


def test_fsm_no_path_back_to_ground_states():
    f = FlightStateMachine()
    f.restore(FlightState.ASCENT, t_liftoff=0.0, max_baro_alt=0.0)
    f.step(_inp(0.5, accel_axial=50, command="disarm"))
    f.step(_inp(0.51, accel_axial=50, command="safe"))
    assert f.state == FlightState.ASCENT


def test_logger_is_append_only_and_verifiable(tmp_path):
    log = FlightDataLogger()
    log.log_raw(0.0, "baro", 100.0, "OK", "OK")
    log.log_event(0.1, "STATE", "x")
    log.log_estimate(0.1, alt=1.0)
    assert [r.sequence for r in log.raw + log.events + log.estimates] == [1, 2, 3]
    assert log.verify()
    object.__setattr__(log.raw[0], "value", 999.0)   # tamper
    assert not log.verify()
    log.storage_ok = False
    with pytest.raises(StorageError):
        log.log_raw(0.2, "baro", 1.0, "OK", "OK")
    paths = log.export(tmp_path)
    assert paths["raw"].exists() and "sha256_chain" in paths["manifest"].read_text()


def _ident(**kw):
    d = dict(version="FW-1.0.0", build_timestamp="2026-09-01T00:00:00Z", commit_hash="abc",
             config_hash="c" * 64, firmware_hash=sha256_bytes(b"image"))
    d.update(kw)
    return FirmwareIdentity(**d)


def _fc(**kw):
    d = dict(detected=True, identity=_ident(), storage_free_bytes=64 << 20,
             sensor_health={k: Health.OK for k in ("imu_accel", "imu_gyro", "baro", "gnss")},
             battery_v=8.2, clock_valid=True, telemetry_link=True, gnss_sats=10)
    d.update(kw)
    return FlightComputerStatus(**d)


def test_preflight_ready_and_not_ready():
    req = PreflightRequirements(expected_identity=_ident())
    ok = run_preflight(_fc(), req, True, True)
    assert ok.status == "READY"
    assert "SYSTEM STATUS:\nREADY" in ok.render()
    bad_fw = run_preflight(_fc(identity=_ident(firmware_hash="0" * 64)), req, True, True)
    assert bad_fw.status == "NOT READY"
    baro = run_preflight(_fc(sensor_health={"imu_accel": Health.OK, "imu_gyro": Health.OK,
                                            "baro": Health.DEGRADED, "gnss": Health.OK}), req, True, True)
    assert baro.status == "NOT READY"
    warn = run_preflight(_fc(gnss_sats=3), req, True, True)
    assert warn.status == "READY (WITH WARNINGS)"
    assert run_preflight(_fc(), req, True, False).status == "NOT READY"
    assert run_preflight(_fc(battery_v=6.0), req, True, True).status == "NOT READY"
