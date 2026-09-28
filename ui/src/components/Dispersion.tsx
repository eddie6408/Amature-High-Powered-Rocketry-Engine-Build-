import { useState } from "react";

import { fmt, linear, niceTicks } from "./scale";

const S = 360;
const P = 34;

interface Ellipse { mean_east_m: number; mean_north_m: number; semi_major_m: number; semi_minor_m: number;
  major_axis_bearing_deg: number; confidence: number }

/** Monte Carlo landing points around the pad with the 95 % ellipse and the field radius. */
export function Dispersion({ points, ellipse, fieldRadius }: {
  points: Array<[number | null, number | null]>; ellipse: Ellipse | null; fieldRadius?: number | null }) {
  const [hover, setHover] = useState<number | null>(null);
  const pts = points.filter((p): p is [number, number] => p[0] !== null && p[1] !== null);
  const ext = Math.max(50, fieldRadius ?? 0, ...pts.map(([e, n]) => Math.max(Math.abs(e), Math.abs(n))),
    ellipse ? Math.hypot(ellipse.mean_east_m, ellipse.mean_north_m) + ellipse.semi_major_m : 0) * 1.1;
  const x = linear(-ext, ext, P, S - 8);
  const y = linear(-ext, ext, S - P, 8);
  const k = x(1) - x(0);
  const ticks = niceTicks(-ext, ext, 4);
  return (
    <div className="card chart-card">
      <div className="card-head"><h2>Landing dispersion <span className="unit">(m from pad, {pts.length} runs)</span></h2></div>
      <svg viewBox={`0 0 ${S} ${S}`} width="100%" role="img" aria-label="Monte Carlo landing points">
        {ticks.map((t) => (
          <g key={t}>
            <line className="gridline" x1={x(t)} x2={x(t)} y1={8} y2={S - P} />
            <line className="gridline" x1={P} x2={S - 8} y1={y(t)} y2={y(t)} />
            <text x={x(t)} y={S - P + 14} textAnchor="middle">{fmt(t)}</text>
            <text x={P - 4} y={y(t) + 4} textAnchor="end">{fmt(t)}</text>
          </g>
        ))}
        {fieldRadius ? <circle className="field-radius" cx={x(0)} cy={y(0)} r={fieldRadius * k} /> : null}
        {ellipse && (
          <ellipse className="track-est" cx={x(ellipse.mean_east_m)} cy={y(ellipse.mean_north_m)}
                   rx={ellipse.semi_minor_m * k} ry={ellipse.semi_major_m * k}
                   transform={`rotate(${ellipse.major_axis_bearing_deg} ${x(ellipse.mean_east_m)} ${y(ellipse.mean_north_m)})`} />
        )}
        {pts.map(([e, n], i) => (
          <g key={i} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}>
            <circle cx={x(e)} cy={y(n)} r={12} fill="transparent" />
            <circle className={`scatter${hover === i ? " hover" : ""}`} cx={x(e)} cy={y(n)} r={4} />
          </g>
        ))}
        <path className="track-pad" d={`M${x(0)},${y(0) - 7}l6,11h-12z`} />
        <text x={S - 10} y={S - 4} textAnchor="end">E →</text>
        <text x={P + 2} y={18}>N ↑</text>
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ right: 16, top: 34 }}>
          <div className="v">{fmt(pts[hover][0])} m E, {fmt(pts[hover][1])} m N</div>
          <div className="k">run {hover + 1} · {fmt(Math.hypot(...pts[hover]))} m from pad</div>
        </div>
      )}
      <div className="note">▲ pad · ● landing (SIMULATED){ellipse && ` · ◌ ${Math.round(ellipse.confidence * 100)} % ellipse`}{fieldRadius ? " · ○ field radius" : ""}</div>
    </div>
  );
}
