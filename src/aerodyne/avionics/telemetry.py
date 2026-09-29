"""Telemetry protocol TELEMETRY-2 (wire-compatible with firmware/src/telemetry.c).

Frame (little-endian, 55 bytes):

  off size field
    0  2  sync             0xD1AE  (bytes AE D1)
    2  1  version          2
    3  2  vehicle_id
    5  2  flight_id
    7  4  sequence         monotonic u32
   11  4  timestamp_ms     flight computer monotonic clock
   15  4  altitude_m       f32, filtered AGL
   19  4  velocity_mps     f32, filtered vertical
   23  4  accel_mps2       f32, axial specific force
   27  4  lat_e7           i32 deg*1e7
   31  4  lon_e7           i32 deg*1e7
   35  8  attitude         4 x i16, quaternion * 32767
   43  2  battery_mv       u16
   45  2  temperature_c10  i16, 0.1 C
   47  1  system_status    u8, flight state
   48  2  sensor_status    u16, 2 bits of Health per sensor slot
   50  1  nav_status       u8, bit0 baro used, bit1 accel used, bit2 gnss used
   51  1  gnss_fix         u8, 0 none 2 2D 3 3D
   52  1  gnss_sats        u8
   53  2  crc16            CRC-16/CCITT-FALSE over bytes 0..52

The receiver tolerates packet loss, duplication, reordering, corruption and
link interruptions, and counts each.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

SYNC = 0xD1AE
VERSION = 2
_FMT = "<HBHHIIfffii4hHhBHBBB"
_BODY = struct.calcsize(_FMT)
FRAME_SIZE = _BODY + 2
SENSOR_SLOTS = ("imu_accel", "imu_gyro", "baro", "gnss", "battery", "temperature", "storage", "radio")


# Identity frame (sync 0xD2AE, 117 bytes): the flight computer periodically
# reports who it is so the ground station can verify firmware before flight.
#   0 sync u16 | 2 version u8 | 3 vehicle_id u16 | 5 flight_id u16 | 7 sequence u32
#  11 firmware_version char[16] | 27 commit char[12] | 39 firmware_hash u8[32]
#  71 config_hash u8[32] | 103 hardware_version char[12] | 115 crc16
ID_SYNC = 0xD2AE
_ID_FMT = "<HBHHI16s12s32s32s12s"
_ID_BODY = struct.calcsize(_ID_FMT)
ID_FRAME_SIZE = _ID_BODY + 2


def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass(frozen=True)
class TelemetryPacket:
    vehicle_id: int
    flight_id: int
    sequence: int
    timestamp_ms: int
    altitude: float
    velocity: float
    acceleration: float
    latitude: float
    longitude: float
    attitude: tuple[float, float, float, float]
    battery_mv: int
    temperature_c: float
    system_status: int
    sensor_status: int
    nav_status: int
    gnss_fix: int
    gnss_sats: int

    def encode(self) -> bytes:
        q = tuple(max(-32767, min(32767, round(c * 32767))) for c in self.attitude)
        body = struct.pack(
            _FMT, SYNC, VERSION, self.vehicle_id & 0xFFFF, self.flight_id & 0xFFFF,
            self.sequence & 0xFFFFFFFF, self.timestamp_ms & 0xFFFFFFFF, self.altitude,
            self.velocity, self.acceleration, round(self.latitude * 1e7),
            round(self.longitude * 1e7), *q, self.battery_mv & 0xFFFF,
            max(-32768, min(32767, round(self.temperature_c * 10))), self.system_status & 0xFF,
            self.sensor_status & 0xFFFF, self.nav_status & 0xFF, self.gnss_fix & 0xFF,
            self.gnss_sats & 0xFF)
        return body + struct.pack("<H", crc16_ccitt(body))

    @classmethod
    def decode(cls, frame: bytes) -> "TelemetryPacket":
        if len(frame) != FRAME_SIZE:
            raise ValueError("bad frame length")
        (crc,) = struct.unpack("<H", frame[_BODY:])
        if crc16_ccitt(frame[:_BODY]) != crc:
            raise ValueError("crc mismatch")
        f = struct.unpack(_FMT, frame[:_BODY])
        if f[0] != SYNC or f[1] != VERSION:
            raise ValueError("bad sync/version")
        return cls(vehicle_id=f[2], flight_id=f[3], sequence=f[4], timestamp_ms=f[5],
                   altitude=f[6], velocity=f[7], acceleration=f[8], latitude=f[9] / 1e7,
                   longitude=f[10] / 1e7, attitude=tuple(x / 32767 for x in f[11:15]),
                   battery_mv=f[15], temperature_c=f[16] / 10, system_status=f[17],
                   sensor_status=f[18], nav_status=f[19], gnss_fix=f[20], gnss_sats=f[21])


@dataclass(frozen=True)
class IdentityPacket:
    vehicle_id: int
    flight_id: int
    sequence: int
    firmware_version: str
    commit: str
    firmware_hash: str        # hex; all zeros = not provisioned
    config_hash: str          # hex; all zeros = not provisioned
    hardware_version: str

    def encode(self) -> bytes:
        body = struct.pack(_ID_FMT, ID_SYNC, VERSION, self.vehicle_id & 0xFFFF, self.flight_id & 0xFFFF,
                           self.sequence & 0xFFFFFFFF, self.firmware_version.encode()[:16],
                           self.commit.encode()[:12], bytes.fromhex(self.firmware_hash.rjust(64, "0")),
                           bytes.fromhex(self.config_hash.rjust(64, "0")), self.hardware_version.encode()[:12])
        return body + struct.pack("<H", crc16_ccitt(body))

    @classmethod
    def decode(cls, frame: bytes) -> "IdentityPacket":
        if len(frame) != ID_FRAME_SIZE:
            raise ValueError("bad identity frame length")
        (crc,) = struct.unpack("<H", frame[_ID_BODY:])
        if crc16_ccitt(frame[:_ID_BODY]) != crc:
            raise ValueError("crc mismatch")
        f = struct.unpack(_ID_FMT, frame[:_ID_BODY])
        if f[0] != ID_SYNC or f[1] != VERSION:
            raise ValueError("bad sync/version")
        txt = lambda b: b.split(b"\0", 1)[0].decode(errors="replace")
        return cls(f[2], f[3], f[4], txt(f[5]), txt(f[6]), f[7].hex(), f[8].hex(), txt(f[9]))

    @property
    def provisioned(self) -> bool:
        return any(c != "0" for c in self.firmware_hash)


def pack_sensor_status(health: dict[str, int]) -> int:
    word = 0
    for i, name in enumerate(SENSOR_SLOTS):
        word |= (int(health.get(name, 3)) & 0x3) << (2 * i)
    return word


def unpack_sensor_status(word: int) -> dict[str, int]:
    return {name: (word >> (2 * i)) & 0x3 for i, name in enumerate(SENSOR_SLOTS)}


def _seq_newer(a: int, b: int) -> bool:
    """Serial-number arithmetic (RFC 1982) for u32 sequences: is a after b?"""
    return 0 < ((a - b) & 0xFFFFFFFF) < 0x80000000


@dataclass
class LinkStats:
    frames_ok: int = 0
    crc_failures: int = 0
    bytes_discarded: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    lost: int = 0
    link_interruptions: int = 0
    identity_frames: int = 0

    @property
    def packet_loss_rate(self) -> float:
        total = self.frames_ok - self.duplicates + self.lost
        return self.lost / total if total else 0.0


@dataclass
class TelemetryReceiver:
    """Byte-stream receiver: resynchronizes on corruption, drops duplicates,
    accepts late packets (flagged), counts gaps as losses until filled."""

    vehicle_id: int | None = None
    link_timeout: float = 2.0
    dedupe_window: int = 1024
    stats: LinkStats = field(default_factory=LinkStats)
    _buf: bytearray = field(default_factory=bytearray)
    _highest: int | None = None
    _seen: dict[int, None] = field(default_factory=dict)
    _missing: set[int] = field(default_factory=set)
    _last_rx: float | None = None
    link_up: bool = False
    identity: IdentityPacket | None = None

    def feed(self, data: bytes, rx_time: float) -> list[tuple[TelemetryPacket, bool]]:
        """Returns [(packet, in_order)] accepted from this chunk."""
        if self._last_rx is not None and rx_time - self._last_rx > self.link_timeout:
            self.stats.link_interruptions += 1
        self._buf.extend(data)
        out: list[tuple[TelemetryPacket, bool]] = []
        syncs = (struct.pack("<H", SYNC), struct.pack("<H", ID_SYNC))
        while True:
            found = [i for i in (self._buf.find(s) for s in syncs) if i >= 0]
            if not found:
                keep = 1 if self._buf[-1:] == syncs[0][:1] else 0
                self.stats.bytes_discarded += len(self._buf) - keep
                del self._buf[:len(self._buf) - keep]
                break
            idx = min(found)
            if idx > 0:
                self.stats.bytes_discarded += idx
                del self._buf[:idx]
            is_id = bytes(self._buf[:2]) == syncs[1]
            size = ID_FRAME_SIZE if is_id else FRAME_SIZE
            if len(self._buf) < size:
                break
            frame = bytes(self._buf[:size])
            try:
                pkt = IdentityPacket.decode(frame) if is_id else TelemetryPacket.decode(frame)
            except ValueError:
                self.stats.crc_failures += 1
                del self._buf[:1]          # resync: skip this sync word
                continue
            del self._buf[:size]
            if is_id:
                if self.vehicle_id is None or pkt.vehicle_id == self.vehicle_id:
                    self.identity = pkt
                    self.stats.identity_frames += 1
                    self._last_rx = rx_time
                continue
            if self.vehicle_id is not None and pkt.vehicle_id != self.vehicle_id:
                continue
            res = self._accept(pkt)
            if res is not None:
                out.append((pkt, res))
                self._last_rx = rx_time
                self.link_up = True
        return out

    def _accept(self, pkt: TelemetryPacket) -> bool | None:
        self.stats.frames_ok += 1
        s = pkt.sequence
        if s in self._seen:
            self.stats.duplicates += 1
            return None
        self._seen[s] = None
        if len(self._seen) > self.dedupe_window:
            self._seen.pop(next(iter(self._seen)))
        if self._highest is None:
            self._highest = s
            return True
        if _seq_newer(s, self._highest):
            gap = (s - self._highest - 1) & 0xFFFFFFFF
            if gap:
                for k in range(1, min(gap, 10000) + 1):
                    self._missing.add((self._highest + k) & 0xFFFFFFFF)
                self.stats.lost += gap
            self._highest = s
            return True
        # older than the newest seen: a late (out-of-order) packet
        self.stats.out_of_order += 1
        if s in self._missing:
            self._missing.discard(s)
            self.stats.lost -= 1
        return False

    def check_link(self, now: float) -> bool:
        if self._last_rx is None or now - self._last_rx > self.link_timeout:
            self.link_up = False
        return self.link_up
