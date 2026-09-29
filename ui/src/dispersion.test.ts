import { describe, expect, it } from "vitest";

import { circleOutline, ellipseOutline, fractionInside, offsetToLatLon } from "./dispersion";

describe("dispersion geometry", () => {
  it("puts the major axis along its bearing", () => {
    const e = { mean_east_m: 100, mean_north_m: -50, semi_major_m: 300, semi_minor_m: 100, major_axis_bearing_deg: 90,
      confidence: 0.95, max_range_m: 500 };
    const pts = ellipseOutline(e, 4);
    expect(pts[0][0]).toBeCloseTo(400, 6);        // bearing 90 deg: major axis east-west
    expect(pts[0][1]).toBeCloseTo(-50, 6);
    expect(pts[1][1]).toBeCloseTo(-150, 6);       // minor axis north-south
    const north = ellipseOutline({ ...e, major_axis_bearing_deg: 0 }, 4);
    expect(north[0][1]).toBeCloseTo(250, 6);
    expect(pts[4][0]).toBeCloseTo(pts[0][0], 9); expect(pts[4][1]).toBeCloseTo(pts[0][1], 9);   // closed
  });
  it("circle, offsets and containment", () => {
    const c = circleOutline(1000, 8);
    expect(Math.hypot(...c[3])).toBeCloseTo(1000, 6);
    const [lat, lon] = offsetToLatLon(40, -105, 1000, 1000);
    expect(lat - 40).toBeCloseTo(0.008993, 5);
    expect(lon + 105).toBeCloseTo(0.011740, 5);
    expect(fractionInside([[0, 10], [600, 0], [0, -900]], 700)).toBeCloseTo(2 / 3, 9);
    expect(fractionInside([], 5)).toBe(0);
  });
});
