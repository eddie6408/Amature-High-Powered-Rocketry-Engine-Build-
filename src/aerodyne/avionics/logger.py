"""Flight data recorder.

Raw sensor records (timestamp, sensor ID, value, status, sequence) are kept
separately from system events and from derived estimates. The store is
append-only; a SHA-256 chain makes after-the-fact edits detectable.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class StorageError(IOError):
    pass


@dataclass(frozen=True)
class RawRecord:
    sequence: int
    timestamp: float
    sensor_id: str
    value: Any
    status: str
    health: str


@dataclass(frozen=True)
class EventRecord:
    sequence: int
    timestamp: float
    event: str
    detail: str = ""


@dataclass(frozen=True)
class EstimateRecord:
    sequence: int
    timestamp: float
    values: dict[str, float]


@dataclass
class FlightDataLogger:
    raw: list[RawRecord] = field(default_factory=list)
    events: list[EventRecord] = field(default_factory=list)
    estimates: list[EstimateRecord] = field(default_factory=list)
    storage_ok: bool = True
    _seq: int = 0
    _chain: str = "0" * 64
    dropped: int = 0

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    def _extend_chain(self, obj: Any) -> None:
        blob = json.dumps(obj, sort_keys=True, default=str).encode()
        self._chain = hashlib.sha256(self._chain.encode() + blob).hexdigest()

    def _write(self, target: list, rec: Any) -> None:
        if not self.storage_ok:
            self.dropped += 1
            raise StorageError("storage unavailable")
        target.append(rec)
        self._extend_chain(rec.__dict__)

    def log_raw(self, timestamp: float, sensor_id: str, value: Any, status: str, health: str) -> None:
        self._write(self.raw, RawRecord(self._next(), timestamp, sensor_id,
                                        list(value) if isinstance(value, tuple) else value,
                                        status, health))

    def log_event(self, timestamp: float, event: str, detail: str = "") -> None:
        self._write(self.events, EventRecord(self._next(), timestamp, event, detail))

    def log_estimate(self, timestamp: float, **values: float) -> None:
        self._write(self.estimates, EstimateRecord(self._next(), timestamp, dict(values)))

    @property
    def digest(self) -> str:
        return self._chain

    def verify(self) -> bool:
        """Recompute the hash chain over stored records in sequence order."""
        chain = "0" * 64
        recs = sorted(self.raw + self.events + self.estimates, key=lambda r: r.sequence)
        for r in recs:
            blob = json.dumps(r.__dict__, sort_keys=True, default=str).encode()
            chain = hashlib.sha256(chain.encode() + blob).hexdigest()
        return chain == self._chain

    def export(self, directory: str | Path) -> dict[str, Path]:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        paths = {"raw": d / "raw.csv", "events": d / "events.csv",
                 "estimates": d / "estimates.jsonl", "manifest": d / "manifest.json"}
        with open(paths["raw"], "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["sequence", "timestamp", "sensor_id", "value", "status", "health"])
            for r in self.raw:
                w.writerow([r.sequence, f"{r.timestamp:.6f}", r.sensor_id, json.dumps(r.value),
                            r.status, r.health])
        with open(paths["events"], "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["sequence", "timestamp", "event", "detail"])
            for e in self.events:
                w.writerow([e.sequence, f"{e.timestamp:.6f}", e.event, e.detail])
        with open(paths["estimates"], "w") as fh:
            for e in self.estimates:
                fh.write(json.dumps({"sequence": e.sequence, "timestamp": e.timestamp,
                                     "kind": "DERIVED", **e.values}) + "\n")
        paths["manifest"].write_text(json.dumps({
            "raw_records": len(self.raw), "events": len(self.events),
            "estimates": len(self.estimates), "dropped_writes": self.dropped,
            "sha256_chain": self.digest}, indent=2))
        return paths
