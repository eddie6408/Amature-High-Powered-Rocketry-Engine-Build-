"""Flight data ingestion and time normalization.

Sources (flight-computer logs, telemetry, GNSS, environment) are kept as
separate channels with their provenance; normalization aligns them onto a
common timebase (liftoff = 0) without modifying the raw arrays.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from aerodyne.core.provenance import DataKind


@dataclass(frozen=True)
class Channel:
    name: str
    t: np.ndarray
    values: np.ndarray          # (N,) or (N, k)
    units: str
    source: str
    kind: DataKind = DataKind.MEASURED


@dataclass
class FlightDataset:
    flight_id: str
    channels: dict[str, Channel] = field(default_factory=dict)
    time_offset: float = 0.0          # subtract from raw time to get t since liftoff
    notes: list[str] = field(default_factory=list)

    def add(self, ch: Channel) -> None:
        if ch.name in self.channels:
            raise ValueError(f"channel {ch.name} already present - use a distinct name per source")
        order = np.argsort(ch.t, kind="stable")
        if np.any(np.diff(ch.t) < 0):
            self.notes.append(f"{ch.name}: samples were out of order; sorted by timestamp")
        self.channels[ch.name] = Channel(ch.name, np.asarray(ch.t, float)[order],
                                         np.asarray(ch.values, float)[order], ch.units, ch.source,
                                         ch.kind)

    def t(self, name: str) -> np.ndarray:
        return self.channels[name].t - self.time_offset

    def v(self, name: str) -> np.ndarray:
        return self.channels[name].values

    def align_to_liftoff(self, accel_channel: str = "accel_axial", threshold: float = 2 * 9.80665,
                         sustain: float = 0.1) -> float:
        ch = self.channels[accel_channel]
        a = ch.values if ch.values.ndim == 1 else ch.values[:, 0]
        above = a > threshold
        dt = float(np.median(np.diff(ch.t)))
        n = max(1, int(round(sustain / dt)))
        run = np.convolve(above.astype(int), np.ones(n, int), mode="valid")
        idx = np.nonzero(run >= n)[0]
        if len(idx) == 0:
            raise ValueError("no liftoff found in accelerometer channel")
        self.time_offset = float(ch.t[idx[0]])
        return self.time_offset

    def resample(self, names: list[str], rate: float) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """Common timebase over the overlap of the channels; linear interpolation,
        never extrapolation."""
        t0 = max(self.t(n)[0] for n in names)
        t1 = min(self.t(n)[-1] for n in names)
        tt = np.arange(t0, t1, 1.0 / rate)
        out = {}
        for n in names:
            v = self.v(n)
            if v.ndim == 1:
                out[n] = np.interp(tt, self.t(n), v)
            else:
                out[n] = np.column_stack([np.interp(tt, self.t(n), v[:, k]) for k in range(v.shape[1])])
        return tt, out


def load_logger_export(directory: str | Path, flight_id: str) -> FlightDataset:
    """Read the raw.csv written by :meth:`FlightDataLogger.export`. Only samples
    whose logged health was OK are used for the analysis channels; everything is
    still in the raw file."""
    d = Path(directory)
    rows: dict[str, list[tuple[float, object]]] = {}
    with open(d / "raw.csv", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["health"] != "OK":
                continue
            rows.setdefault(r["sensor_id"], []).append((float(r["timestamp"]), json.loads(r["value"])))
    return dataset_from_samples(rows, flight_id, source=str(d))


def dataset_from_samples(rows: dict[str, list[tuple[float, object]]], flight_id: str,
                         source: str, kind: DataKind = DataKind.MEASURED) -> FlightDataset:
    ds = FlightDataset(flight_id)
    if "imu_accel" in rows:
        t = np.array([x[0] for x in rows["imu_accel"]])
        v = np.array([x[1] for x in rows["imu_accel"]], dtype=float)
        ds.add(Channel("accel", t, v, "m/s^2", source, kind))
        ds.add(Channel("accel_axial", t, v[:, 0], "m/s^2", source, kind))
    if "imu_gyro" in rows:
        ds.add(Channel("gyro", np.array([x[0] for x in rows["imu_gyro"]]),
                       np.array([x[1] for x in rows["imu_gyro"]], dtype=float), "rad/s", source, kind))
    if "baro" in rows:
        ds.add(Channel("baro_alt", np.array([x[0] for x in rows["baro"]]),
                       np.array([x[1] for x in rows["baro"]], dtype=float), "m MSL", source, kind))
    if "gnss" in rows:
        ds.add(Channel("gnss", np.array([x[0] for x in rows["gnss"]]),
                       np.array([x[1] for x in rows["gnss"]], dtype=float), "deg,deg,m", source, kind))
    return ds


def logger_to_dataset(logger, flight_id: str, kind: DataKind = DataKind.MEASURED) -> FlightDataset:
    rows: dict[str, list[tuple[float, object]]] = {}
    for r in logger.raw:
        if r.health == "OK":
            rows.setdefault(r.sensor_id, []).append((r.timestamp, r.value))
    return dataset_from_samples(rows, flight_id, source="flight-computer log", kind=kind)
