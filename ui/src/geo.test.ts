import { describe, expect, it } from "vitest";

import { fmtCoords, locationError, parseCoord } from "./geo";

describe("parseCoord", () => {
  it("reads decimal degrees with sign or hemisphere", () => {
    expect(parseCoord("40.1234", "lat")).toBeCloseTo(40.1234, 6);
    expect(parseCoord("-105.2", "lon")).toBeCloseTo(-105.2, 6);
    expect(parseCoord("105.2 W", "lon")).toBeCloseTo(-105.2, 6);
    expect(parseCoord("s 33.5", "lat")).toBeCloseTo(-33.5, 6);
    expect(parseCoord("40,5", "lat")).toBeCloseTo(40.5, 6);
  });
  it("reads degrees-minutes and degrees-minutes-seconds", () => {
    expect(parseCoord("40 7.404 N", "lat")).toBeCloseTo(40.1234, 4);
    expect(parseCoord("40°7'24.24\"N", "lat")).toBeCloseTo(40.1234, 4);
    expect(parseCoord("105° 12′ 30″ W", "lon")).toBeCloseTo(-105.208333, 5);
  });
  it("rejects nonsense and out-of-range values", () => {
    for (const [s, a] of [["", "lat"], ["abc", "lat"], ["91", "lat"], ["181", "lon"], ["40 N", "lon"],
      ["105 E", "lat"], ["40 61 N", "lat"], ["-40 S", "lat"], ["4-0", "lat"], ["1 2 3 4", "lat"]] as const) {
      expect(parseCoord(s, a), s).toBeNull();
    }
  });
});

describe("formatting and messages", () => {
  it("formats hemispheres", () => {
    expect(fmtCoords(40.1, -105.25, 2)).toBe("40.10° N, 105.25° W");
    expect(fmtCoords(-33.9, 151.2, 1)).toBe("33.9° S, 151.2° E");
  });
  it("always offers manual entry", () => {
    for (const c of [1, 2, 3, 99, "unsupported", "insecure"] as const) expect(locationError(c)).toMatch(/by hand/);
  });
});
