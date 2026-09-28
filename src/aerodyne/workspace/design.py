"""A vehicle design as stored in a revision: geometry + recovery + avionics.

The motor is *not* part of the airframe revision - the same revision can fly
different motors; the motor is chosen per mission and recorded per flight.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from aerodyne.recovery.recovery import RecoveryConfig, RecoveryDevice
from aerodyne.twin.digital_twin import AvionicsConfig
from aerodyne.vehicle.vehicle import Vehicle


@dataclass
class Design:
    vehicle: Vehicle
    recovery: RecoveryConfig | None = None
    avionics: AvionicsConfig = field(default_factory=AvionicsConfig)
    notes: str = ""


def recovery_to_dict(rc: RecoveryConfig | None) -> dict[str, Any] | None:
    if rc is None:
        return None
    return {"body_cd_area": rc.body_cd_area,
            "devices": [dataclasses.asdict(d) for d in rc.devices]}


def recovery_from_dict(d: dict[str, Any] | None) -> RecoveryConfig | None:
    if not d or not d.get("devices"):
        return None
    return RecoveryConfig(devices=tuple(RecoveryDevice(**x) for x in d["devices"]),
                          body_cd_area=float(d.get("body_cd_area", 0.01)))


def design_to_payload(d: Design) -> dict[str, Any]:
    av = dataclasses.asdict(d.avionics)
    av["sensors"] = list(av["sensors"])
    return {"vehicle": d.vehicle.to_dict(), "recovery": recovery_to_dict(d.recovery),
            "avionics": av, "notes": d.notes}


def design_from_payload(p: dict[str, Any]) -> Design:
    av = dict(p.get("avionics") or {})
    if "sensors" in av:
        av["sensors"] = tuple(av["sensors"])
    return Design(vehicle=Vehicle.from_dict(p["vehicle"]), recovery=recovery_from_dict(p.get("recovery")),
                  avionics=AvionicsConfig(**av), notes=p.get("notes", ""))
