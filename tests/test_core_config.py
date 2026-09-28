import pytest

from aerodyne.config import ConfigurationError, FlightConfiguration, VehicleRegistry, revision_label
from aerodyne.core import RecordStatus, stable_hash


def test_revision_labels():
    assert revision_label(0) == "REV-A"
    assert revision_label(25) == "REV-Z"
    assert revision_label(26) == "REV-AA"
    assert revision_label(27) == "REV-AB"


def test_stable_hash_is_order_independent():
    assert stable_hash({"a": 1, "b": [1, 2]}) == stable_hash({"b": [1, 2], "a": 1})
    assert stable_hash({"a": 1}) != stable_hash({"a": 2})


def _flight_cfg(reg, vid, label):
    rev = reg.get(vid).revision(label)
    return FlightConfiguration(vid, label, rev.config_hash, "FC-HW-001", "FW-1.0.0", "f" * 64,
                               "abc123", "SENSOR-CONFIG-001", "s" * 64, "TELEMETRY-2")


def test_vehicle_ids_and_revisions(tmp_path):
    reg = VehicleRegistry()
    v1 = reg.create_vehicle("one", {"mass": 1.0}, author="eng")
    v2 = reg.create_vehicle("two", {"mass": 2.0}, author="eng")
    assert (v1.vehicle_id, v2.vehicle_id) == ("AERODYNE-001", "AERODYNE-002")
    rev_b = reg.revise("AERODYNE-001", {"mass": 1.1}, "added ballast", author="eng")
    assert rev_b.label == "REV-B" and rev_b.parent == "REV-A"
    assert v1.revision("REV-A").meta.status == RecordStatus.FROZEN
    with pytest.raises(ConfigurationError):
        reg.update_in_place("AERODYNE-001", "REV-A", {"mass": 5})
    with pytest.raises(ConfigurationError):
        reg.revise("AERODYNE-001", {"mass": 1.1}, "no-op", author="eng")

    reg.record_flight("F-001", _flight_cfg(reg, "AERODYNE-001", "REV-B"))
    assert rev_b.is_flown
    with pytest.raises(ConfigurationError):
        reg.update_in_place("AERODYNE-001", "REV-B", {"mass": 9})
    with pytest.raises(ConfigurationError):
        reg.record_flight("F-001", _flight_cfg(reg, "AERODYNE-001", "REV-B"))

    path = tmp_path / "reg.json"
    reg.save(path)
    loaded = VehicleRegistry.load(path)
    assert loaded.get("AERODYNE-001").latest.config_hash == rev_b.config_hash
    assert loaded.flight("F-001").firmware_version == "FW-1.0.0"


def test_tampered_registry_detected(tmp_path):
    reg = VehicleRegistry()
    reg.create_vehicle("one", {"mass": 1.0}, author="eng")
    path = tmp_path / "reg.json"
    reg.save(path)
    path.write_text(path.read_text().replace('"mass": 1.0', '"mass": 9.0'))
    with pytest.raises(ConfigurationError):
        VehicleRegistry.load(path)


def test_flight_config_hash_must_match():
    reg = VehicleRegistry()
    reg.create_vehicle("one", {"mass": 1.0}, author="eng")
    bad = FlightConfiguration("AERODYNE-001", "REV-A", "0" * 64, "FC-HW-001", "FW-1", "f", "c",
                              "S", "s", "TELEMETRY-2")
    with pytest.raises(ConfigurationError):
        reg.record_flight("F-1", bad)
