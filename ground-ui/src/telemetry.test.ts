import { describe, expect, it } from "vitest";

import fixture from "./__fixtures__/sil_corrupted.json";
import { GroundStation } from "./station";
import { crc16ccitt, decode, DecodeError, FRAME_SIZE, tiltDeg, unpackSensorStatus } from "./telemetry";

const b64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

describe("TELEMETRY-2 decoder", () => {
  it("matches the CRC-16/CCITT-FALSE check value", () => {
    expect(crc16ccitt(new TextEncoder().encode("123456789"))).toBe(0x29b1);
  });

  it("decodes a frame produced by the flight software", () => {
    const frame = fixture.frames.map((f) => b64(f.b64)).find((b) => b.length === FRAME_SIZE)!;
    const p = decode(frame);
    const exp = fixture.expected.firstPacket;
    expect(p.sequence).toBe(exp.sequence);
    expect(p.altitude).toBeCloseTo(exp.altitude, 3);
    expect(p.latitude).toBeCloseTo(exp.latitude, 7);
    expect(p.longitude).toBeCloseTo(exp.longitude, 7);
    expect(p.batteryMv).toBe(exp.batteryMv);
    expect(p.sensorStatus).toBe(exp.sensorStatus);
  });

  it("rejects a corrupted frame", () => {
    const frame = b64(fixture.frames[0].b64).slice();
    frame[20] ^= 0x10;
    expect(() => decode(frame)).toThrow(DecodeError);
  });

  it("unpacks sensor health and attitude", () => {
    expect(unpackSensorStatus(0b01_00_00_00).gnss).toBe("DEGRADED"); // slot 3 = gnss
    expect(unpackSensorStatus(0b10_00_00_00).gnss).toBe("FAILED");
    expect(tiltDeg([1, 0, 0, 0])).toBeCloseTo(90); // body x horizontal
    const s = Math.SQRT1_2;
    expect(tiltDeg([s, 0, -s, 0])).toBeCloseTo(0, 5); // body x rotated to +z: vertical
  });
});

describe("ground station matches the Python reference", () => {
  const gs = new GroundStation(1, fixture.plan);
  const checkpoints = new Map(fixture.expected.checkpoints.map((c) => [c.index, c]));
  const seen: Array<{ index: number; est: ReturnType<GroundStation["landingEstimate"]>;
                      state: string; note: string }> = [];
  fixture.frames.forEach((f, i) => {
    gs.feed(b64(f.b64), f.t);
    if (checkpoints.has(i)) {
      const est = gs.landingEstimate();
      seen.push({ index: i, est, state: gs.state, note: gs.estimateNote });
    }
  });

  it("link statistics (loss, duplicates, reordering, corruption)", () => {
    const s = gs.rx.stats;
    const e = fixture.expected.stats;
    expect({ framesOk: s.framesOk, crcFailures: s.crcFailures, duplicates: s.duplicates,
             outOfOrder: s.outOfOrder, lost: s.lost, linkInterruptions: s.linkInterruptions })
      .toEqual(e);
    expect(gs.packets.length).toBe(fixture.expected.packetsAccepted); // incl. late packets
  });

  it("flight state and max altitude", () => {
    expect(gs.state).toBe(fixture.expected.finalState);
    expect(gs.maxAltitude).toBeCloseTo(fixture.expected.maxAltitude, 2);
  });

  it("landing estimates at every checkpoint", () => {
    expect(seen.length).toBe(fixture.expected.checkpoints.length);
    for (const s of seen) {
      const c = checkpoints.get(s.index)!;
      expect(s.state).toBe(c.state);
      expect(s.note).toBe(c.note);
      if (c.estimate === null) {
        expect(s.est).toBeNull();
      } else {
        expect(s.est).not.toBeNull();
        expect(s.est!.eastM).toBeCloseTo(c.estimate.eastM, 1);
        expect(s.est!.northM).toBeCloseTo(c.estimate.northM, 1);
        expect(s.est!.radiusM).toBeCloseTo(c.estimate.radiusM, 1);
        expect(s.est!.timeToGroundS).toBeCloseTo(c.estimate.timeToGroundS, 2);
        expect(s.est!.basis).toBe(c.estimate.basis);
      }
    }
  });
});
