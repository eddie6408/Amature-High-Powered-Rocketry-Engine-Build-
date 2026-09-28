/**
 * Ground-station state: vehicle status, history for plots, GNSS track and a
 * landing estimate that never claims more precision than the data supports.
 * Mirrors src/aerodyne/ground/station.py (same algorithm, same fixture tests).
 */
import {
  STATES, TelemetryPacket, TelemetryReceiver, tiltDeg, unpackSensorStatus,
  type Health, type SensorSlot,
} from "./telemetry";

const EARTH_R = 6371000;

export interface DescentPlan {
  mainDeployAltAgl: number;
  mainDescentRate: number; // m/s, positive
}

export interface Sample {
  t: number; // vehicle time, s
  altitude: number;
  velocity: number;
  acceleration: number;
  state: string;
}

export interface TrackPoint {
  t: number;
  lat: number;
  lon: number;
  alt: number;
}

export interface LandingEstimate {
  eastM: number;
  northM: number;
  radiusM: number;
  timeToGroundS: number;
  gnssQuality: string;
  basis: string;
  kind: "ESTIMATED";
}

export function gnssQuality(fix: number, sats: number): { label: string; sigma: number } {
  if (fix < 2) return { label: "NO FIX", sigma: Infinity };
  if (fix === 2 || sats < 6) return { label: "POOR", sigma: 25 };
  if (sats < 9) return { label: "FAIR", sigma: 8 };
  return { label: "GOOD", sigma: 4 };
}

export class GroundStation {
  rx: TelemetryReceiver;
  latest: TelemetryPacket | null = null;
  state = "NO DATA";
  maxAltitude = 0;
  history: Sample[] = [];
  packets: TelemetryPacket[] = [];
  track: TrackPoint[] = [];
  launch: { lat: number; lon: number } | null = null;
  lastPacketRx: number | null = null;
  now = 0;
  descentT0: number | null = null;
  estimateNote = "";

  constructor(vehicleId: number | null = null, public plan: DescentPlan | null = null,
              linkTimeout = 2.0) {
    this.rx = new TelemetryReceiver(vehicleId, linkTimeout);
  }

  feed(data: Uint8Array, now: number): number {
    this.now = now;
    let n = 0;
    for (const { packet, inOrder } of this.rx.feed(data, now)) {
      this.packets.push(packet);
      n++;
      if (inOrder) this.apply(packet, now);
    }
    return n;
  }

  private apply(p: TelemetryPacket, now: number): void {
    this.latest = p;
    this.state = p.systemStatus <= 6 ? STATES[p.systemStatus] : `?${p.systemStatus}`;
    this.maxAltitude = Math.max(this.maxAltitude, p.altitude);
    this.lastPacketRx = now;
    const t = p.timestampMs / 1000;
    if (this.state === "DESCENT" && this.descentT0 === null) this.descentT0 = t;
    this.history.push({ t, altitude: p.altitude, velocity: p.velocity,
                        acceleration: p.acceleration, state: this.state });
    if (p.gnssFix >= 2) {
      if (this.launch === null && p.systemStatus <= 2) this.launch = { lat: p.latitude, lon: p.longitude };
      this.track.push({ t, lat: p.latitude, lon: p.longitude, alt: p.altitude });
    }
  }

  get sensorHealth(): Record<SensorSlot, Health> | null {
    return this.latest ? unpackSensorStatus(this.latest.sensorStatus) : null;
  }

  get tilt(): number | null {
    return this.latest ? tiltDeg(this.latest.attitude) : null;
  }

  linkStatus(): string {
    if (this.lastPacketRx === null) return "NO LINK";
    const age = this.now - this.lastPacketRx;
    return age <= this.rx.linkTimeout ? "LINK OK" : `LINK LOST (${age.toFixed(0)} s)`;
  }

  en(lat: number, lon: number): [number, number] {
    const l = this.launch ?? { lat, lon };
    return [
      ((lon - l.lon) * Math.PI) / 180 * EARTH_R * Math.cos((l.lat * Math.PI) / 180),
      ((lat - l.lat) * Math.PI) / 180 * EARTH_R,
    ];
  }

  landingEstimate(window = 5.0): LandingEstimate | null {
    this.estimateNote = "";
    const p = this.latest;
    if (this.state !== "DESCENT" || !this.track.length || !p || !p.velocity) return null;
    if (!(p.navStatus & 1)) {
      this.estimateNote = "landing estimate unavailable: altitude not baro-aided";
      return null;
    }
    if (p.velocity >= -0.5) return null;
    const tLast = this.track[this.track.length - 1].t;
    const settle = (this.descentT0 ?? tLast) + 2.0;
    const pts = this.track.filter((q) => tLast - q.t <= window && q.t >= settle);
    if (pts.length < 10 || pts[pts.length - 1].t - pts[0].t < 0.6 * window) return null;
    const ts = pts.map((q) => q.t);
    const en = pts.map((q) => this.en(q.lat, q.lon));
    const tm = ts.reduce((a, b) => a + b, 0) / ts.length;
    const sxx = ts.reduce((a, t) => a + (t - tm) ** 2, 0);
    const ve = ts.reduce((a, t, i) => a + (t - tm) * en[i][0], 0) / sxx;
    const vn = ts.reduce((a, t, i) => a + (t - tm) * en[i][1], 0) / sxx;
    const eNow = en.reduce((a, v) => a + v[0], 0) / en.length + ve * (tLast - tm);
    const nNow = en.reduce((a, v) => a + v[1], 0) / en.length + vn * (tLast - tm);
    const alt = Math.max(this.track[this.track.length - 1].alt, 0);
    const vNow = -p.velocity;
    let tGround: number;
    let tSpread: number;
    let basis = "current descent rate";
    if (this.plan && vNow > 1.5 * this.plan.mainDescentRate) {
      const h = this.plan.mainDeployAltAgl;
      tGround = Math.max(alt - h, 0) / vNow + Math.min(alt, h) / this.plan.mainDescentRate;
      tSpread = (0.25 * h) / this.plan.mainDescentRate;
      basis = "descent plan";
    } else if (this.plan === null) {
      const tFast = alt / vNow;
      const tSlow = alt / Math.min(vNow, 3.0);
      tGround = 0.5 * (tFast + tSlow);
      tSpread = 0.5 * (tSlow - tFast) + 0.1 * tFast;
      basis = vNow > 8 ? "PRELIMINARY (no descent plan)" : "current descent rate, no plan";
    } else {
      tGround = alt / vNow;
      tSpread = (0.1 * alt) / vNow;
    }
    const { label, sigma } = gnssQuality(p.gnssFix, p.gnssSats);
    const sigmaV = sigma / Math.sqrt(sxx);
    const drift = Math.hypot(ve, vn);
    const radius = 2 * Math.hypot(sigma, sigmaV * tGround) + drift * tSpread + 0.3 * tGround * drift + 10;
    return { eastM: eNow + ve * tGround, northM: nNow + vn * tGround, radiusM: radius,
             timeToGroundS: tGround, gnssQuality: label, basis, kind: "ESTIMATED" };
  }
}
