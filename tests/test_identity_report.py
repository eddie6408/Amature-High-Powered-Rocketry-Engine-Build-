"""Firmware identity over telemetry (spec 38/39/43) and the flight report."""

import pytest

from aerodyne.avionics.telemetry import IdentityPacket, TelemetryReceiver
from aerodyne.ground import GroundStation
from aerodyne.reporting.reports import flight_report
from aerodyne.sil import cbackend
from aerodyne.sil.runner import SILRunner


def _ident(h="ab" * 32):
    return IdentityPacket(1, 7, 42, "FW-1.2.0", "abcdef123456", h, "cd" * 32, "FC-HW-002")


def test_identity_roundtrip_and_corruption():
    f = _ident().encode()
    assert len(f) == 117 and IdentityPacket.decode(f) == _ident()
    bad = bytearray(f)
    bad[60] ^= 1
    rx = TelemetryReceiver()
    rx.feed(bytes(bad) + b"xx" + f, 0.0)
    assert rx.identity == _ident() and rx.stats.crc_failures == 1 and rx.stats.identity_frames == 1


def test_ground_firmware_check():
    gs = GroundStation()
    assert gs.firmware_check(None)[0] == "FAIL"                 # nothing heard yet
    gs.feed(_ident().encode(), 0.0)
    assert gs.firmware_check({"firmware_hash": "ab" * 32})[0] == "PASS"
    assert gs.firmware_check({"firmware_hash": "00" * 31 + "01"})[0] == "FAIL"
    assert gs.firmware_check({"firmware_hash": ""})[0] == "WARN"
    gs2 = GroundStation()
    gs2.feed(_ident("00" * 32).encode(), 0.0)
    assert "not provisioned" in gs2.firmware_check({"firmware_hash": "ab" * 32})[1]


@pytest.mark.parametrize("backend", ["python", "c-app"])
def test_flight_software_reports_identity(nominal_sim, backend):
    if backend == "c-app" and not cbackend.available():
        pytest.skip("C flight software not built")
    r = SILRunner(nominal_sim, [], backend=backend).run()
    assert r.evaluate() == []
    gs = GroundStation()
    for t, data in r.radio_frames:
        gs.feed(data, t)
    assert gs.rx.stats.identity_frames >= 15               # every 5 s over a ~120 s session
    assert gs.rx.identity.hardware_version == "FC-HW-001"
    assert gs.status.state == "LANDED"                     # telemetry unaffected


def test_flight_report_markdown():
    flight = {"flight_id": "F-9", "date": "2026-10-01", "vehicle_id": "AERODYNE-001", "revision": "REV-A",
              "motor": {"manufacturer": "X", "designation": "H1", "data_quality": "CERTIFIED"},
              "configuration": {"firmware_version": "FW-1", "firmware_hash": "ab"}, "configuration_hash": "cafe",
              "raw_files": [{"name": "log.csv", "bytes": 10, "sha256": "f00d", "source": "upload"}]}
    md = flight_report(flight, None)
    assert "# Flight report F-9" in md and "`f00d`" in md and "Not analysed" in md
    analysis = {"actual_kind": "DERIVED", "file": "log.csv", "prediction_basis": "mission", "status": "VALIDATED",
                "comparison": [{"label": "Apogee", "units": "m", "simulated": 900.0, "actual": 880.0,
                                "abs_error": 20.0, "pct_error": 2.27}],
                "phases": {"apogee": 12.3}, "contributors": [{"name": "wind", "evidence": "e",
                                                              "suggested_check": "c"}],
                "notes": ["n1"], "motor_quality": "CERTIFIED"}
    md = flight_report(flight, analysis)
    assert "| Apogee [m] | 900.00 | 880.00 | 20.00 | +2.3 % |" in md and "**wind**" in md
