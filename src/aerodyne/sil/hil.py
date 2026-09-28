"""Hardware-in-the-loop interface.

    FLIGHT SIMULATOR -> SIMULATED SENSORS -> REAL FLIGHT COMPUTER
                     -> REAL FIRMWARE -> REAL TELEMETRY

The HIL bridge streams virtual sensor samples to the flight computer over a
transport (UART/USB/CAN) using the frame below, and reads the real telemetry
back through :class:`aerodyne.avionics.telemetry.TelemetryReceiver`. The
firmware must be built with ``AERO_HIL=1`` so its sensor task reads injected
samples instead of the physical drivers (everything downstream - validation,
navigation, state machine, logging, telemetry - is the flight code path).

Sensor-injection frame (little-endian): sync 0xA5 0x5A, u8 sensor_slot,
u32 sequence, f64 timestamp, u8 n_values, n x f32 values, u8 status, u16 CRC-16/CCITT.
"""

from __future__ import annotations

import struct
from typing import Protocol

from aerodyne.avionics.sensors import SensorReading
from aerodyne.avionics.telemetry import SENSOR_SLOTS, TelemetryPacket, TelemetryReceiver, crc16_ccitt


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...
    def read(self, max_bytes: int) -> bytes: ...


def encode_injection(r: SensorReading) -> bytes:
    vals = r.value if isinstance(r.value, tuple) else (r.value,)
    body = struct.pack("<BBBId B", 0xA5, 0x5A, SENSOR_SLOTS.index(r.kind), r.sequence & 0xFFFFFFFF,
                       r.timestamp, len(vals)) + struct.pack(f"<{len(vals)}f", *vals) + bytes(
        [int(r.status)])
    return body + struct.pack("<H", crc16_ccitt(body))


class HILBridge:
    def __init__(self, transport: Transport, vehicle_id: int | None = None) -> None:
        self.transport = transport
        self.receiver = TelemetryReceiver(vehicle_id=vehicle_id)

    def inject(self, readings: dict[str, SensorReading]) -> None:
        for r in readings.values():
            self.transport.write(encode_injection(r))

    def poll(self, now: float) -> list[TelemetryPacket]:
        data = self.transport.read(4096)
        return [p for p, _ in self.receiver.feed(data, now)] if data else []
