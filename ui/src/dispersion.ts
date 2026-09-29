/** Landing-dispersion geometry shared by the maps (pure functions, unit-tested). */

export interface Ellipse { mean_east_m: number; mean_north_m: number; semi_major_m: number; semi_minor_m: number;
  major_axis_bearing_deg: number; confidence: number; max_range_m: number }
export interface Dispersion { points: Array<[number, number]>; ellipse: Ellipse | null; field_radius_m: number | null;
  run_id: string | null; current: boolean; runs?: number; created?: string;
  site: { latitude: number; longitude: number; altitude_msl: number } }

const R_EARTH = 6371008.8;

/** East/north offsets (m) of the ellipse outline; the major axis points along its bearing from north. */
export function ellipseOutline(e: Ellipse, n = 72): Array<[number, number]> {
  const th = (e.major_axis_bearing_deg * Math.PI) / 180;
  const out: Array<[number, number]> = [];
  for (let i = 0; i <= n; i++) {
    const p = (2 * Math.PI * i) / n;
    const a = e.semi_major_m * Math.cos(p), b = e.semi_minor_m * Math.sin(p);
    out.push([e.mean_east_m + a * Math.sin(th) + b * Math.cos(th), e.mean_north_m + a * Math.cos(th) - b * Math.sin(th)]);
  }
  return out;
}

export function circleOutline(radius: number, n = 96): Array<[number, number]> {
  return Array.from({ length: n + 1 }, (_, i) => [radius * Math.sin((2 * Math.PI * i) / n), radius * Math.cos((2 * Math.PI * i) / n)]);
}

/** Small-offset east/north (m) -> latitude/longitude (deg) around a reference point. */
export function offsetToLatLon(lat0: number, lon0: number, east: number, north: number): [number, number] {
  const lat = lat0 + (north / R_EARTH) * (180 / Math.PI);
  const lon = lon0 + (east / (R_EARTH * Math.cos((lat0 * Math.PI) / 180))) * (180 / Math.PI);
  return [lat, lon];
}

/** Fraction of landing points inside a circle of the given radius around the pad. */
export function fractionInside(points: Array<[number, number]>, radius: number): number {
  if (!points.length) return 0;
  return points.filter(([e, n]) => Math.hypot(e, n) <= radius).length / points.length;
}
