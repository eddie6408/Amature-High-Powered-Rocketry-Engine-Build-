import { useMemo, useRef, useState } from "react";

import { decimate, fmt, linear, niceTicks, tickDigits } from "./scale";

export interface Point {
  t: number;
  v: number;
}

export interface Marker {
  t: number;
  label: string;
}

export interface Series {
  name: string;
  data: Point[];
  slot?: 1 | 2;            // categorical slot (colour), fixed per entity - never by rank
  dashed?: boolean;
}

interface Props {
  title: string;
  unit: string;
  data?: Point[];          // single-series shorthand
  series?: Series[];       // up to two series (predicted vs actual)
  markers?: Marker[];
  digits?: number;
  height?: number;
  xLabel?: string;
}

const M = { top: 18, right: 12, bottom: 24, left: 48 };
const W = 640;
const LABEL_W = 58;

function placeMarkers(ms: Marker[], x: (t: number) => number) {
  let lastEnd = -Infinity;
  return ms.map((m) => {
    const px = x(m.t);
    const anchorEnd = px + LABEL_W > W - M.right;
    const start = anchorEnd ? px - LABEL_W : px;
    const showLabel = start >= lastEnd + 4;
    if (showLabel) lastEnd = anchorEnd ? px : px + LABEL_W;
    return { ...m, anchorEnd, showLabel };
  });
}

/** End-of-line labels; when two would collide only the legend identifies the series. */
function directLabels(drawn: Point[][], x: (t: number) => number, y: (v: number) => number) {
  const ends = drawn.map((d) => (d.length ? { x: Math.min(x(d[d.length - 1].t), W - M.right) - 2, y: y(d[d.length - 1].v) - 6 } : null));
  const clash = ends.length === 2 && ends[0] && ends[1] && Math.abs(ends[0].y - ends[1].y) < 14 && Math.abs(ends[0].x - ends[1].x) < 70;
  return clash ? ends.map(() => null) : ends;
}

const nearestIdx = (pts: Point[], t: number) => {
  let lo = 0;
  let hi = pts.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (pts[mid].t < t) lo = mid;
    else hi = mid;
  }
  return Math.abs(pts[lo].t - t) <= Math.abs(pts[hi].t - t) ? lo : hi;
};

/** Time chart, one y-axis. One series: no legend (the title names it). Two
 * series: legend + direct end labels. Crosshair tooltip lists every series. */
export function LineChart({ title, unit, data, series, markers = [], digits = 0, height = 180, xLabel = "s" }: Props) {
  const [hoverT, setHoverT] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const all: Series[] = useMemo(
    () => (series ?? [{ name: title, data: data ?? [], slot: 1 }]).map((s, i) => ({
      ...s, slot: s.slot ?? ((i + 1) as 1 | 2), data: s.data.filter((p) => Number.isFinite(p.v) && Number.isFinite(p.t)),
    })),
    [series, data, title],
  );
  const drawn = useMemo(() => all.map((s) => decimate(s.data, (p) => p.v)), [all]);
  const pts = all.flatMap((s) => s.data);

  const t0 = pts.length ? Math.min(...all.filter((s) => s.data.length).map((s) => s.data[0].t)) : 0;
  const t1 = pts.length ? Math.max(...all.filter((s) => s.data.length).map((s) => s.data[s.data.length - 1].t)) : 1;
  const vals = drawn.flat().map((p) => p.v);
  let vmin = Math.min(0, ...vals);
  let vmax = Math.max(0, ...vals);
  if (vmin === vmax) vmax = vmin + 1;
  const yTicks = niceTicks(vmin, vmax, 4);
  vmin = Math.min(vmin, yTicks[0] ?? vmin);
  vmax = Math.max(vmax, yTicks[yTicks.length - 1] ?? vmax);
  const x = linear(t0, t1 > t0 ? t1 : t0 + 1, M.left, W - M.right);
  const y = linear(vmin, vmax, height - M.bottom, M.top);
  const xTicks = niceTicks(t0, t1, 6);
  const yd = tickDigits(yTicks);
  const xd = tickDigits(xTicks);
  const multi = all.length > 1;

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!pts.length || !svgRef.current) return;
    const r = svgRef.current.getBoundingClientRect();
    const sx = ((e.clientX - r.left) / r.width) * W;
    setHoverT(t0 + ((sx - M.left) / (W - M.left - M.right)) * (t1 - t0));
  };
  const step = (t1 - t0) / 200;
  const onKey = (e: React.KeyboardEvent) => {
    if (!pts.length) return;
    const k = e.shiftKey ? 10 : 1;
    if (e.key === "ArrowRight") setHoverT((h) => Math.min(t1, (h ?? t0) + k * step));
    else if (e.key === "ArrowLeft") setHoverT((h) => Math.max(t0, (h ?? t1) - k * step));
    else if (e.key === "Escape") setHoverT(null);
    else return;
    e.preventDefault();
  };
  const hover = hoverT === null ? null : all.map((s) => (s.data.length ? s.data[nearestIdx(s.data, hoverT)] : null));
  const tipLeftPct = hoverT !== null ? (x(Math.min(Math.max(hoverT, t0), t1)) / W) * 100 : 0;

  return (
    <div className="card chart-card">
      <div className="card-head">
        <h2>{title} <span className="unit">({unit})</span></h2>
        {multi && (
          <div className="legend">
            {all.map((s) => (
              <span key={s.name}><span className={`key s${s.slot}${s.dashed ? " dashed" : ""}`} />{s.name}</span>
            ))}
          </div>
        )}
      </div>
      {pts.length === 0 ? (
        <div className="empty">No data yet</div>
      ) : (
        <>
          <svg ref={svgRef} className="chart" viewBox={`0 0 ${W} ${height}`} width="100%" role="img"
               aria-label={`${title}; use arrow keys to read values`} tabIndex={0}
               onPointerMove={onMove} onPointerLeave={() => setHoverT(null)} onKeyDown={onKey}
               onBlur={() => setHoverT(null)}>
            {yTicks.map((v) => (
              <g key={`y${v}`}>
                <line className={v === 0 ? "baseline" : "gridline"} x1={M.left} x2={W - M.right} y1={y(v)} y2={y(v)} />
                <text x={M.left - 6} y={y(v) + 4} textAnchor="end">{fmt(v, yd)}</text>
              </g>
            ))}
            {xTicks.map((t) => (
              <text key={`x${t}`} x={x(t)} y={height - 6} textAnchor="middle">{fmt(t, xd)} {xLabel}</text>
            ))}
            {placeMarkers(markers.filter((m) => m.t >= t0 && m.t <= t1), x).map((m) => (
              <g key={`${m.label}${m.t}`}>
                <line className="marker-line" x1={x(m.t)} x2={x(m.t)} y1={M.top} y2={height - M.bottom} />
                {m.showLabel && (
                  <text className="marker-label" x={m.anchorEnd ? x(m.t) - 3 : x(m.t) + 3} y={M.top - 6}
                        textAnchor={m.anchorEnd ? "end" : "start"}>{m.label}</text>
                )}
              </g>
            ))}
            {drawn.map((d, i) => (
              <path key={all[i].name} className={`series s${all[i].slot}${all[i].dashed ? " dashed" : ""}`}
                    d={d.map((p, k) => `${k ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("")} />
            ))}
            {multi && directLabels(drawn, x, y).map((l, i) => l && (
              <text key={`lbl${i}`} className="direct-label" x={l.x} y={l.y} textAnchor="end">{all[i].name}</text>
            ))}
            {hover && hoverT !== null && (
              <g>
                <line className="crosshair" x1={x(Math.min(Math.max(hoverT, t0), t1))}
                      x2={x(Math.min(Math.max(hoverT, t0), t1))} y1={M.top} y2={height - M.bottom} />
                {hover.map((p, i) => p && (
                  <circle key={i} className={`dot s${all[i].slot}`} cx={x(p.t)} cy={y(p.v)} r={4} />
                ))}
              </g>
            )}
          </svg>
          {hover && hoverT !== null && (
            <div className="tooltip" style={{ left: `calc(${Math.min(tipLeftPct, 78)}% + 8px)`, top: 34 }}>
              {hover.map((p, i) => p && (
                <div key={i}>
                  <div className="v">{fmt(p.v, digits)} {unit}</div>
                  <div className="k"><span className={`key s${all[i].slot}`} />{all[i].name} · {fmt(p.t, 2)} {xLabel}</div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
