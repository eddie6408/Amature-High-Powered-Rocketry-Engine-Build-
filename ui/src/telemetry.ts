/**
 * TELEMETRY-2 decoder and link receiver.
 * Wire-compatible with src/aerodyne/avionics/telemetry.py and
 * firmware/src/aero_telemetry.c (55-byte little-endian frame, CRC-16/CCITT-FALSE).
 */

export const SYNC = 0xd1ae;
export const VERSION = 2;
export const FRAME_SIZE = 55;
export const SENSOR_SLOTS = [
  "imu_accel", "imu_gyro", "baro", "gnss", "battery", "temperature", "storage", "radio",
] as const;
export type SensorSlot = (typeof SENSOR_SLOTS)[number];
export const HEALTH = ["OK", "DEGRADED", "FAILED", "UNKNOWN"] as const;
export type Health = (typeof HEALTH)[number];
export const STATES = ["SAFE", "PREFLIGHT", "ARMED", "ASCENT", "COAST", "DESCENT", "LANDED"] as const;

export interface TelemetryPacket {
  vehicleId: number;
  flightId: number;
  sequence: number;
  timestampMs: number;
  altitude: number;
  velocity: number;
  acceleration: number;
  latitude: number;
  longitude: number;
  attitude: [number, number, number, number];
  batteryMv: number;
  temperatureC: number;
  systemStatus: number;
  sensorStatus: number;
  navStatus: number;
  gnssFix: number;
  gnssSats: number;
}

export const ID_SYNC = 0xd2ae;
export const ID_FRAME_SIZE = 117;

export interface IdentityPacket {
  vehicleId: number; flightId: number; sequence: number; firmwareVersion: string; commit: string;
  firmwareHash: string; configHash: string; hardwareVersion: string;
}

const hex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
const cstr = (b: Uint8Array) => { const z = b.indexOf(0); return new TextDecoder().decode(z < 0 ? b : b.subarray(0, z)); };

export function decodeIdentity(frame: Uint8Array): IdentityPacket {
  if (frame.length !== ID_FRAME_SIZE) throw new DecodeError("bad identity frame length");
  const dv = new DataView(frame.buffer, frame.byteOffset, frame.byteLength);
  if (crc16ccitt(frame.subarray(0, ID_FRAME_SIZE - 2)) !== dv.getUint16(ID_FRAME_SIZE - 2, true)) throw new DecodeError("crc mismatch");
  if (dv.getUint16(0, true) !== ID_SYNC || dv.getUint8(2) !== VERSION) throw new DecodeError("bad sync/version");
  return { vehicleId: dv.getUint16(3, true), flightId: dv.getUint16(5, true), sequence: dv.getUint32(7, true),
           firmwareVersion: cstr(frame.subarray(11, 27)), commit: cstr(frame.subarray(27, 39)),
           firmwareHash: hex(frame.subarray(39, 71)), configHash: hex(frame.subarray(71, 103)),
           hardwareVersion: cstr(frame.subarray(103, 115)) };
}

export function crc16ccitt(data: Uint8Array, crc = 0xffff): number {
  for (const b of data) {
    crc ^= b << 8;
    for (let i = 0; i < 8; i++) {
      crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xffff : (crc << 1) & 0xffff;
    }
  }
  return crc;
}

export class DecodeError extends Error {}

export function decode(frame: Uint8Array): TelemetryPacket {
  if (frame.length !== FRAME_SIZE) throw new DecodeError("bad frame length");
  const dv = new DataView(frame.buffer, frame.byteOffset, frame.byteLength);
  const crc = dv.getUint16(FRAME_SIZE - 2, true);
  if (crc16ccitt(frame.subarray(0, FRAME_SIZE - 2)) !== crc) throw new DecodeError("crc mismatch");
  if (dv.getUint16(0, true) !== SYNC || dv.getUint8(2) !== VERSION) {
    throw new DecodeError("bad sync/version");
  }
  const q = [0, 1, 2, 3].map((i) => dv.getInt16(35 + 2 * i, true) / 32767) as [
    number, number, number, number,
  ];
  return {
    vehicleId: dv.getUint16(3, true),
    flightId: dv.getUint16(5, true),
    sequence: dv.getUint32(7, true),
    timestampMs: dv.getUint32(11, true),
    altitude: dv.getFloat32(15, true),
    velocity: dv.getFloat32(19, true),
    acceleration: dv.getFloat32(23, true),
    latitude: dv.getInt32(27, true) / 1e7,
    longitude: dv.getInt32(31, true) / 1e7,
    attitude: q,
    batteryMv: dv.getUint16(43, true),
    temperatureC: dv.getInt16(45, true) / 10,
    systemStatus: dv.getUint8(47),
    sensorStatus: dv.getUint16(48, true),
    navStatus: dv.getUint8(50),
    gnssFix: dv.getUint8(51),
    gnssSats: dv.getUint8(52),
  };
}

export function unpackSensorStatus(word: number): Record<SensorSlot, Health> {
  const out = {} as Record<SensorSlot, Health>;
  SENSOR_SLOTS.forEach((name, i) => {
    out[name] = HEALTH[(word >> (2 * i)) & 3];
  });
  return out;
}

/** Tilt of the body axis from vertical, degrees (attitude quaternion body->ENU). */
export function tiltDeg(q: [number, number, number, number]): number {
  const [w, x, y, z] = q;
  const n = Math.hypot(w, x, y, z) || 1;
  const up = (2 * (x * z - w * y)) / (n * n); // world-z component of body x axis
  return (Math.acos(Math.max(-1, Math.min(1, up))) * 180) / Math.PI;
}

export interface LinkStats {
  framesOk: number;
  crcFailures: number;
  bytesDiscarded: number;
  duplicates: number;
  outOfOrder: number;
  lost: number;
  linkInterruptions: number;
  identityFrames: number;
}

const seqNewer = (a: number, b: number) => {
  const d = (a - b) >>> 0;
  return d > 0 && d < 0x80000000;
};

/** Byte-stream receiver: resync on corruption, drop duplicates, flag late
 * packets, count gaps as losses until filled. Mirrors TelemetryReceiver in Python. */
export class TelemetryReceiver {
  stats: LinkStats = {
    framesOk: 0, crcFailures: 0, bytesDiscarded: 0, duplicates: 0, outOfOrder: 0, lost: 0,
    linkInterruptions: 0, identityFrames: 0,
  };
  identity: IdentityPacket | null = null;
  private buf = new Uint8Array(0);
  private highest: number | null = null;
  private seen = new Set<number>();
  private seenOrder: number[] = [];
  private missing = new Set<number>();
  private lastRx: number | null = null;

  constructor(
    public vehicleId: number | null = null,
    public linkTimeout = 2.0,
    private dedupeWindow = 1024,
  ) {}

  feed(data: Uint8Array, rxTime: number): Array<{ packet: TelemetryPacket; inOrder: boolean }> {
    if (this.lastRx !== null && rxTime - this.lastRx > this.linkTimeout) this.stats.linkInterruptions++;
    const merged = new Uint8Array(this.buf.length + data.length);
    merged.set(this.buf);
    merged.set(data, this.buf.length);
    let b = merged;
    const out: Array<{ packet: TelemetryPacket; inOrder: boolean }> = [];
    for (;;) {
      let idx = -1;
      for (let i = 0; i + 1 < b.length; i++) {
        if (b[i] === 0xae && (b[i + 1] === 0xd1 || b[i + 1] === 0xd2)) { idx = i; break; }
      }
      if (idx < 0) {
        const keep = b.length && b[b.length - 1] === 0xae ? 1 : 0;
        this.stats.bytesDiscarded += b.length - keep;
        b = b.slice(b.length - keep);
        break;
      }
      if (idx > 0) {
        this.stats.bytesDiscarded += idx;
        b = b.slice(idx);
      }
      const isId = b[1] === 0xd2;
      const size = isId ? ID_FRAME_SIZE : FRAME_SIZE;
      if (b.length < size) break;
      let pkt: TelemetryPacket;
      try {
        if (isId) {
          const id = decodeIdentity(b.slice(0, size));
          b = b.slice(size);
          if (this.vehicleId === null || id.vehicleId === this.vehicleId) {
            this.identity = id;
            this.stats.identityFrames++;
            this.lastRx = rxTime;
          }
          continue;
        }
        pkt = decode(b.slice(0, size));
      } catch {
        this.stats.crcFailures++;
        b = b.slice(1);
        continue;
      }
      b = b.slice(size);
      if (this.vehicleId !== null && pkt.vehicleId !== this.vehicleId) continue;
      const res = this.accept(pkt);
      if (res !== null) {
        out.push({ packet: pkt, inOrder: res });
        this.lastRx = rxTime;
      }
    }
    this.buf = b;
    return out;
  }

  private accept(pkt: TelemetryPacket): boolean | null {
    this.stats.framesOk++;
    const s = pkt.sequence;
    if (this.seen.has(s)) {
      this.stats.duplicates++;
      return null;
    }
    this.seen.add(s);
    this.seenOrder.push(s);
    if (this.seenOrder.length > this.dedupeWindow) this.seen.delete(this.seenOrder.shift()!);
    if (this.highest === null) {
      this.highest = s;
      return true;
    }
    if (seqNewer(s, this.highest)) {
      const gap = (s - this.highest - 1) >>> 0;
      for (let k = 1; k <= Math.min(gap, 10000); k++) this.missing.add((this.highest + k) >>> 0);
      this.stats.lost += gap;
      this.highest = s;
      return true;
    }
    this.stats.outOfOrder++;
    if (this.missing.delete(s)) this.stats.lost--;
    return false;
  }

  linkUp(now: number): boolean {
    return this.lastRx !== null && now - this.lastRx <= this.linkTimeout;
  }
}
