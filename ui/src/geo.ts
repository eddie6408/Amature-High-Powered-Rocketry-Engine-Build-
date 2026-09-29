/** Launch-location helpers: coordinate parsing/formatting and location-service messages. */

export type Axis = "lat" | "lon";

/**
 * Parse a latitude or longitude typed by hand. Accepts decimal degrees ("40.1234", "-105.2"),
 * a hemisphere letter ("40.1234 N", "W 105.2"), degrees + decimal minutes ("40 7.404 N") and
 * degrees/minutes/seconds ("40°7'24.2\"N"). Returns null when it can't be read or is out of range.
 */
export function parseCoord(text: string, axis: Axis): number | null {
  let s = text.trim().toUpperCase().replace(/,/g, ".");
  if (!s) return null;
  let sign = 1;
  const hemi = s.match(/[NSEW]/g);
  if (hemi) {
    if (hemi.length > 1) return null;
    const h = hemi[0];
    if ((axis === "lat" && (h === "E" || h === "W")) || (axis === "lon" && (h === "N" || h === "S"))) return null;
    if (h === "S" || h === "W") sign = -1;
    s = s.replace(/[NSEW]/, " ");
  }
  if (s.includes("-")) {
    if (hemi || !/^\s*-/.test(s) || (s.match(/-/g) ?? []).length > 1) return null;
    sign *= -1;
    s = s.replace("-", " ");
  }
  s = s.replace(/^\s*\+/, " ");
  const parts = s.replace(/[°º'’′"”″]/g, " ").trim().split(/\s+/).filter(Boolean);
  if (parts.length < 1 || parts.length > 3 || !parts.every((p) => /^\d+(\.\d+)?$|^\.\d+$/.test(p))) return null;
  const [d, m = 0, sec = 0] = parts.map(Number);
  if (parts.length > 1 && (m >= 60 || sec >= 60 || !Number.isInteger(d))) return null;
  if (parts.length > 2 && !Number.isInteger(m)) return null;
  const v = sign * (d + m / 60 + sec / 3600);
  const lim = axis === "lat" ? 90 : 180;
  return Math.abs(v) <= lim ? v : null;
}

/** 40.12345° N, 105.20833° W */
export function fmtCoords(lat: number, lon: number, digits = 5): string {
  return `${Math.abs(lat).toFixed(digits)}° ${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(digits)}° ${lon >= 0 ? "E" : "W"}`;
}

/** Plain-language reason a location request failed (GeolocationPositionError codes). */
export function locationError(code: number | "unsupported" | "insecure"): string {
  switch (code) {
    case "unsupported": return "This browser has no location service. Enter the pad coordinates by hand.";
    case "insecure": return "Browsers only share location with pages on https:// or on this computer (localhost). "
      + "Open the app on the laptop itself, or enter the pad coordinates by hand.";
    case 1: return "Location permission was denied. Allow location for this page, or enter the pad coordinates by hand.";
    case 2: return "No position available (no GPS fix or no service). Enter the pad coordinates by hand.";
    case 3: return "Location timed out (weak or no signal). Try again in the open, or enter the pad coordinates by hand.";
    default: return "Location unavailable. Enter the pad coordinates by hand.";
  }
}
