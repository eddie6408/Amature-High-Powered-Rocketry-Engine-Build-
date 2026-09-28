"""Turn a ground-station telemetry capture into an analysis dataset.

A capture holds the raw radio bytes exactly as received. They are decoded with
the same TELEMETRY-2 receiver the ground station uses (CRC-checked, duplicates
and late packets handled). The altitude in each packet is the flight
computer's on-board Kalman estimate, so that channel is DERIVED, not a raw
sensor; accelerations are the latest raw axial accelerometer sample.
"""

from __future__ import annotations

import math

import numpy as np

from aerodyne.analysis.ingestion import Channel, FlightDataset
from aerodyne.avionics.telemetry import TelemetryReceiver
from aerodyne.core.provenance import DataKind


def dataset_from_frames(frames: list[tuple[float, bytes]], flight_id: str, source: str) -> FlightDataset:
    rx = TelemetryReceiver()
    pkts = []
    for t, data in frames:
        pkts += [p for p, _ in rx.feed(data, t)]
    if len(pkts) < 10:
        raise ValueError(f"only {len(pkts)} valid telemetry packets in capture")
    pkts.sort(key=lambda p: p.sequence)
    t = np.array([p.timestamp_ms / 1000.0 for p in pkts])
    keep = np.concatenate([[True], np.diff(t) > 0])
    pkts = [p for p, k in zip(pkts, keep) if k]
    t = t[keep]
    ds = FlightDataset(flight_id)
    ds.add(Channel("baro_alt", t, np.array([p.altitude for p in pkts]), "m AGL (on-board estimate)",
                   source, DataKind.DERIVED))
    acc = np.array([p.acceleration for p in pkts])
    good = np.isfinite(acc)
    if good.sum() > 10:
        ds.add(Channel("accel", t[good], np.column_stack([acc[good], np.zeros(good.sum()),
                                                          np.zeros(good.sum())]), "m/s^2", source))
        ds.add(Channel("accel_axial", t[good], acc[good], "m/s^2", source))
    gps = [(tt, p.latitude, p.longitude) for tt, p in zip(t, pkts) if p.gnss_fix >= 2]
    if len(gps) >= 2:
        g = np.array(gps)
        ds.add(Channel("gnss", g[:, 0], np.column_stack([g[:, 1], g[:, 2], np.full(len(g), math.nan)]),
                       "deg,deg,m", source))
    st = rx.stats
    ds.notes.append(f"telemetry capture: {len(pkts)} packets, {st.lost} lost, {st.crc_failures} CRC "
                    f"failures; altitude is the on-board estimate at the telemetry rate")
    return ds
