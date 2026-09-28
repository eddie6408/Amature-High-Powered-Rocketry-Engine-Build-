/** Small scale/tick helpers (no charting dependency). */
export function niceTicks(min: number, max: number, count = 5): number[] {
  if (!isFinite(min) || !isFinite(max)) return [];
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const span = max - min;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const err = step0 / mag;
  const step = (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1) * mag;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) {
    out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  }
  return out;
}

export function linear(d0: number, d1: number, r0: number, r1: number) {
  const k = d1 === d0 ? 0 : (r1 - r0) / (d1 - d0);
  return (v: number) => r0 + (v - d0) * k;
}

export function fmt(v: number, digits = 0): string {
  return (Math.abs(v) < 0.5 * 10 ** -digits ? 0 : v).toLocaleString(undefined, {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

/** Decimate a long series for drawing, keeping per-bucket min and max. */
export function decimate<T>(pts: T[], value: (p: T) => number, maxPoints = 1500): T[] {
  if (pts.length <= maxPoints) return pts;
  const bucket = Math.ceil(pts.length / (maxPoints / 2));
  const out: T[] = [];
  for (let i = 0; i < pts.length; i += bucket) {
    const seg = pts.slice(i, i + bucket);
    let lo = seg[0];
    let hi = seg[0];
    for (const p of seg) {
      if (value(p) < value(lo)) lo = p;
      if (value(p) > value(hi)) hi = p;
    }
    if (seg.indexOf(lo) <= seg.indexOf(hi)) out.push(lo, hi);
    else out.push(hi, lo);
  }
  return out;
}
