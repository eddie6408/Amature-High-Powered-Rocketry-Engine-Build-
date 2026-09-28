"""MotorDatabase: curated store of motor performance datasets.

Different sources for the same designation are kept side by side - never
merged or silently substituted. Lookups return all candidates ordered by data
quality, and :meth:`MotorDatabase.get` requires the caller to accept a quality.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import numpy as np

from aerodyne.core.provenance import DataQuality
from aerodyne.propulsion.motor import MotorMetadata, MotorPerformance, impulse_class

QUALITY_RANK = {
    DataQuality.CERTIFIED: 0,
    DataQuality.MEASURED: 1,
    DataQuality.MANUFACTURER: 2,
    DataQuality.ESTIMATED: 3,
    DataQuality.UNKNOWN: 4,
    DataQuality.HYPOTHETICAL: 5,
}


class MotorDatabase:
    def __init__(self) -> None:
        self._motors: dict[str, MotorPerformance] = {}

    def __len__(self) -> int:
        return len(self._motors)

    def add(self, motor: MotorPerformance) -> str:
        key = motor.data_hash[:16]
        if key in self._motors:
            raise ValueError(f"identical dataset already stored ({key})")
        self._motors[key] = motor
        return key

    def add_all(self, motors: Iterable[MotorPerformance]) -> list[str]:
        return [self.add(m) for m in motors]

    def by_key(self, key: str) -> MotorPerformance:
        return self._motors[key]

    def candidates(self, designation: str, manufacturer: str | None = None
                   ) -> list[tuple[str, MotorPerformance]]:
        d = designation.lower().replace("-", "").replace(" ", "")
        out = [(k, m) for k, m in self._motors.items()
               if m.metadata.designation.lower().replace("-", "").replace(" ", "") == d
               and (manufacturer is None or m.metadata.manufacturer.lower() == manufacturer.lower())]
        return sorted(out, key=lambda km: QUALITY_RANK[km[1].metadata.data_quality])

    def get(self, designation: str, accept: Iterable[DataQuality],
            manufacturer: str | None = None) -> MotorPerformance:
        """Best dataset whose quality is explicitly accepted by the caller."""
        accepted = set(accept)
        for _, m in self.candidates(designation, manufacturer):
            if m.metadata.data_quality in accepted:
                return m
        found = [m.metadata.data_quality.value for _, m in self.candidates(designation, manufacturer)]
        raise LookupError(f"no {designation} dataset with accepted quality "
                          f"{sorted(q.value for q in accepted)}; available: {found}")

    def search(self, impulse_letter: str | None = None, diameter_mm: float | None = None,
               manufacturer: str | None = None) -> list[MotorPerformance]:
        out = []
        for m in self._motors.values():
            if impulse_letter and impulse_class(m.total_impulse) != impulse_letter.upper():
                continue
            if diameter_mm and (m.metadata.diameter_mm is None
                                or abs(m.metadata.diameter_mm - diameter_mm) > 0.6):
                continue
            if manufacturer and m.metadata.manufacturer.lower() != manufacturer.lower():
                continue
            out.append(m)
        return sorted(out, key=lambda m: m.total_impulse)

    # ---- persistence ----------------------------------------------------
    def save(self, path: str | Path) -> None:
        rows = []
        for m in self._motors.values():
            meta = dict(m.metadata.__dict__)
            meta["data_quality"] = m.metadata.data_quality.value
            rows.append({"time": m.time.tolist(), "thrust": m.thrust.tolist(),
                         "total_mass": m.total_mass, "propellant_mass": m.propellant_mass,
                         "thrust_uncertainty_rel": m.thrust_uncertainty_rel, "metadata": meta})
        Path(path).write_text(json.dumps(rows, indent=1))

    @classmethod
    def load(cls, path: str | Path) -> "MotorDatabase":
        db = cls()
        for row in json.loads(Path(path).read_text()):
            meta = dict(row["metadata"])
            meta["data_quality"] = DataQuality(meta["data_quality"])
            db.add(MotorPerformance(np.array(row["time"]), np.array(row["thrust"]),
                                    total_mass=row["total_mass"],
                                    propellant_mass=row["propellant_mass"],
                                    metadata=MotorMetadata(**meta),
                                    thrust_uncertainty_rel=row.get("thrust_uncertainty_rel", 0.0)))
        return db
