import { useMemo, useRef, useState } from "react";

import { decimate, fmt, linear, niceTicks } from "./scale";

export interface Point {
  t: number;
  v: number;
}

export interface Marker {
  t: number;
  label: string;
}

interface Props {
  title: string;
  unit: string;
  data: Point[];
  markers?: Marker[];
  digits?: number;
  height?: number;
}

const M = { top: 18, right: 12, bottom: 24, left: 48 };
const W = 640;
const LABEL_W = 58; // approx. width of an event label at 10px

/** Label only events with room; a crowded event keeps its line, and its name
 * is still in the tooltip-free table view. Labels near the right edge flip. */
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

/** Single-series time chart: one axis, 2px line, crosshair + tooltip on hover
 * and keyboard (arrow keys), sparse flight-event markers. */
export function LineChart({ title, unit, data, markers = [], digits = 0, height = 180 }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const pts = useMemo(() => data.filter((p) => Number.isFinite(p.v)), [data]);
  const drawn = useMemo(() => decimate(pts, (p) => p.v), [pts]);

  const t0 = pts.length ? pts[0].t : 0;
  const t1 = pts.length ? pts[pts.length - 1].t : 1;
  let vmin = Math.min(0, ...drawn.map((p) => p.v));
  let vmax = Math.max(0, ...drawn.map((p) => p.v));
  if (vmin === vmax) vmax = vmin + 1;
  const yTicks = niceTicks(vmin, vmax, 4);
  vmin = Math.min(vmin, yTicks[0] ?? vmin);
  vmax = Math.max(vmax, yTicks[yTicks.length - 1] ?? vmax);
  const x = linear(t0, t1 > t0 ? t1 : t0 + 1, M.left, W - M.right);
  const y = linear(vmin, vmax, height - M.bottom, M.top);
  const xTicks = niceTicks(t0, t1, 6);
  const path = drawn.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");

  const nearest = (t: number) => {
    let lo = 0;
    let hi = pts.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (pts[mid].t < t) lo = mid;
      else hi = mid;
    }
    return Math.abs(pts[lo].t - t) <= Math.abs(pts[hi].t - t) ? lo : hi;
  };

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!pts.length || !svgRef.current) return;
    const r = svgRef.current.getBoundingClientRect();
    const sx = ((e.clientX - r.left) / r.width) * W;
    const t = t0 + ((sx - M.left) / (W - M.left - M.right)) * (t1 - t0);
    setHover(nearest(t));
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (!pts.length) return;
    const step = e.shiftKey ? 10 : 1;
    if (e.key === "ArrowRight") setHover((h) => Math.min(pts.length - 1, (h ?? -1) + step));
    else if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? pts.length) - step));
    else if (e.key === "Escape") setHover(null);
    else return;
    e.preventDefault();
  };

  const hp = hover !== null ? pts[hover] : null;
  const tipLeftPct = hp ? (x(hp.t) / W) * 100 : 0;

  return (
    <div className="card">
      <h2>
        {title} <span className="unit">({unit})</span>
      </h2>
      {pts.length === 0 ? (
        <div className="empty">Waiting for telemetry…</div>
      ) : (
        <>
          <svg
            ref={svgRef}
            className="chart"
            viewBox={`0 0 ${W} ${height}`}
            width="100%"
            role="img"
            aria-label={`${title} over time; use arrow keys to read values`}
            tabIndex={0}
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
            onKeyDown={onKey}
            onBlur={() => setHover(null)}
          >
            {yTicks.map((v) => (
              <g key={`y${v}`}>
                <line className={v === 0 ? "baseline" : "gridline"} x1={M.left} x2={W - M.right}
                      y1={y(v)} y2={y(v)} />
                <text x={M.left - 6} y={y(v) + 4} textAnchor="end">{fmt(v)}</text>
              </g>
            ))}
            {xTicks.map((t) => (
              <text key={`x${t}`} x={x(t)} y={height - 6} textAnchor="middle">{fmt(t)} s</text>
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
            <path className="series" d={path} />
            {hp && (
              <g>
                <line className="crosshair" x1={x(hp.t)} x2={x(hp.t)} y1={M.top} y2={height - M.bottom} />
                <circle className="dot" cx={x(hp.t)} cy={y(hp.v)} r={4} />
              </g>
            )}
            <rect x={M.left} y={M.top} width={W - M.left - M.right} height={height - M.top - M.bottom}
                  fill="transparent" />
          </svg>
          {hp && (
            <div className="tooltip" style={{ left: `calc(${Math.min(tipLeftPct, 80)}% + 8px)`, top: 30 }}>
              <div className="v">{fmt(hp.v, digits)} {unit}</div>
              <div className="k"><span className="key" />{title} · t = {fmt(hp.t, 2)} s</div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
