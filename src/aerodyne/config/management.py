"""Vehicle identifiers and revision control.

Rules enforced here:

* every rocket gets a unique ID (``AERODYNE-001``, ``AERODYNE-002`` ...);
* every modification creates a new revision (``REV-A``, ``REV-B`` ... ``REV-Z``,
  ``REV-AA`` ...);
* a revision that has flown is immutable - it can never be overwritten;
* every flight stores the exact hardware/firmware/sensor/telemetry configuration.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aerodyne.core.provenance import RecordMeta, RecordStatus, stable_hash


class ConfigurationError(RuntimeError):
    """Raised when an operation would violate configuration-management rules."""


def revision_label(index: int) -> str:
    """0 -> REV-A, 25 -> REV-Z, 26 -> REV-AA (bijective base-26)."""
    if index < 0:
        raise ValueError("revision index must be >= 0")
    letters = ""
    n = index + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return f"REV-{letters}"


@dataclass
class Revision:
    label: str
    payload: dict[str, Any]
    change_note: str
    meta: RecordMeta
    parent: str | None = None
    flights: list[str] = field(default_factory=list)

    @property
    def config_hash(self) -> str:
        return stable_hash(self.payload)

    @property
    def is_flown(self) -> bool:
        return self.meta.status == RecordStatus.FLOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "payload": self.payload,
            "change_note": self.change_note,
            "parent": self.parent,
            "flights": list(self.flights),
            "config_hash": self.config_hash,
            "meta": self.meta.to_dict(),
        }


@dataclass
class VehicleRecord:
    vehicle_id: str
    name: str
    revisions: list[Revision] = field(default_factory=list)

    @property
    def latest(self) -> Revision:
        return self.revisions[-1]

    def revision(self, label: str) -> Revision:
        for rev in self.revisions:
            if rev.label == label:
                return rev
        raise KeyError(f"{self.vehicle_id} has no revision {label}")


@dataclass(frozen=True)
class FlightConfiguration:
    """Exact configuration of everything that flew. Stored with every flight."""

    vehicle_id: str
    revision: str
    vehicle_config_hash: str
    hardware_version: str        # e.g. FC-HW-001
    firmware_version: str        # e.g. FW-1.0.0
    firmware_hash: str
    firmware_commit: str
    sensor_config: str           # e.g. SENSOR-CONFIG-001
    sensor_config_hash: str
    telemetry_protocol: str      # e.g. TELEMETRY-2

    @property
    def config_hash(self) -> str:
        return stable_hash(self.__dict__)


class VehicleRegistry:
    """In-memory registry with JSON persistence. The PostgreSQL schema in
    ``db/schema.sql`` mirrors this model for multi-user deployments."""

    def __init__(self, prefix: str = "AERODYNE") -> None:
        self.prefix = prefix
        self._vehicles: dict[str, VehicleRecord] = {}
        self._flights: dict[str, FlightConfiguration] = {}

    # ---- vehicles -------------------------------------------------------
    def create_vehicle(self, name: str, payload: dict[str, Any], author: str,
                       source: str = "vehicle-designer") -> VehicleRecord:
        vid = f"{self.prefix}-{len(self._vehicles) + 1:03d}"
        rev = Revision(
            label=revision_label(0),
            payload=copy.deepcopy(payload),
            change_note="initial configuration",
            meta=RecordMeta(author=author, source=source, status=RecordStatus.ACTIVE),
        )
        record = VehicleRecord(vehicle_id=vid, name=name, revisions=[rev])
        self._vehicles[vid] = record
        return record

    def get(self, vehicle_id: str) -> VehicleRecord:
        try:
            return self._vehicles[vehicle_id]
        except KeyError:
            raise KeyError(f"unknown vehicle {vehicle_id}") from None

    def vehicles(self) -> list[VehicleRecord]:
        return list(self._vehicles.values())

    def revise(self, vehicle_id: str, payload: dict[str, Any], change_note: str,
               author: str) -> Revision:
        """Create a new revision. The previous revision is frozen, never edited."""
        if not change_note.strip():
            raise ConfigurationError("a change note is required for every revision")
        record = self.get(vehicle_id)
        prev = record.latest
        if stable_hash(payload) == prev.config_hash:
            raise ConfigurationError("payload identical to latest revision; nothing to revise")
        if prev.meta.status == RecordStatus.ACTIVE:
            prev.meta.status = RecordStatus.FROZEN
            prev.meta.touch()
        rev = Revision(
            label=revision_label(len(record.revisions)),
            payload=copy.deepcopy(payload),
            change_note=change_note,
            parent=prev.label,
            meta=RecordMeta(author=author, source="revision", status=RecordStatus.ACTIVE,
                            version=prev.meta.version + 1),
        )
        record.revisions.append(rev)
        return rev

    def update_in_place(self, vehicle_id: str, label: str, payload: dict[str, Any]) -> None:
        """Edit a draft/active revision. Refused once the revision has flown or is frozen."""
        rev = self.get(vehicle_id).revision(label)
        if rev.meta.status in (RecordStatus.FLOWN, RecordStatus.FROZEN, RecordStatus.RETIRED):
            raise ConfigurationError(
                f"{vehicle_id} {label} is {rev.meta.status.value}; create a new revision instead")
        rev.payload = copy.deepcopy(payload)
        rev.meta.touch()

    # ---- flights --------------------------------------------------------
    def record_flight(self, flight_id: str, config: FlightConfiguration) -> None:
        if flight_id in self._flights:
            raise ConfigurationError(f"flight {flight_id} already recorded")
        rev = self.get(config.vehicle_id).revision(config.revision)
        if rev.config_hash != config.vehicle_config_hash:
            raise ConfigurationError("flight configuration hash does not match the revision")
        rev.meta.status = RecordStatus.FLOWN
        rev.meta.touch()
        rev.flights.append(flight_id)
        self._flights[flight_id] = config

    def flight(self, flight_id: str) -> FlightConfiguration:
        return self._flights[flight_id]

    # ---- persistence ----------------------------------------------------
    def save(self, path: str | Path) -> None:
        data = {
            "prefix": self.prefix,
            "vehicles": [
                {"vehicle_id": v.vehicle_id, "name": v.name,
                 "revisions": [r.to_dict() for r in v.revisions]}
                for v in self._vehicles.values()
            ],
            "flights": {k: dict(v.__dict__) for k, v in self._flights.items()},
        }
        Path(path).write_text(json.dumps(data, indent=2, sort_keys=True))

    @classmethod
    def load(cls, path: str | Path) -> "VehicleRegistry":
        from datetime import datetime

        data = json.loads(Path(path).read_text())
        reg = cls(prefix=data["prefix"])
        for v in data["vehicles"]:
            revs = []
            for r in v["revisions"]:
                m = r["meta"]
                meta = RecordMeta(
                    author=m["author"], source=m["source"], id=m["id"], version=m["version"],
                    created_at=datetime.fromisoformat(m["created_at"]),
                    updated_at=datetime.fromisoformat(m["updated_at"]),
                    status=RecordStatus(m["status"]),
                )
                rev = Revision(label=r["label"], payload=r["payload"], change_note=r["change_note"],
                               meta=meta, parent=r["parent"], flights=list(r["flights"]))
                if rev.config_hash != r["config_hash"]:
                    raise ConfigurationError(
                        f"{v['vehicle_id']} {r['label']}: stored hash mismatch (file tampered?)")
                revs.append(rev)
            reg._vehicles[v["vehicle_id"]] = VehicleRecord(v["vehicle_id"], v["name"], revs)
        for fid, cfg in data["flights"].items():
            reg._flights[fid] = FlightConfiguration(**cfg)
        return reg
