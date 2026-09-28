import { useState } from "react";

import { fmt, linear, niceTicks, tickDigits } from "./scale";

const W = 640;
const H = 200;
const M = { top: 12, right: 12, bottom: 28, left: 40 };

/** Distribution of a Monte Carlo output. Bars in slot 1, 2px gaps, P5/P50/P95 marked. */
export function Histogram({ title, unit, values, bins = 20 }: { title: string; unit: string; values: number[]; bins?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const v = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!v.length) return <div className="card"><h2>{title}</h2><div className="empty">No data</div></div>;
  const lo = v[0];
  const hi = v[v.length - 1] === lo ? lo + 1 : v[v.length - 1];
  const w = (hi - lo) / bins;
  const counts = Array.from({ length: bins }, (_, i) => v.filter((x) => x >= lo + i * w && (i === bins - 1 ? x <= hi : x < lo + (i + 1) * w)).length);
  const cmax = Math.max(...counts);
  const x = linear(lo, hi, M.left, W - M.right);
  const y = linear(0, cmax, H - M.bottom, M.top);
  const q = (p: number) => v[Math.min(v.length - 1, Math.max(0, Math.round((p / 100) * (v.length - 1))))];
  const pcts: Array<[string, number]> = [["P5", q(5)], ["P50", q(50)], ["P95", q(95)]];
  return (
    <div className="card chart-card">
      <div className="card-head"><h2>{title} <span className="unit">({unit}, {v.length} runs)</span></h2></div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`${title} histogram`}>
        {niceTicks(0, cmax, 3).map((c) => (
          <g key={c}><line className="gridline" x1={M.left} x2={W - M.right} y1={y(c)} y2={y(c)} />
            <text x={M.left - 6} y={y(c) + 4} textAnchor="end">{c}</text></g>
        ))}
        {counts.map((c, i) => {
          const bx = x(lo + i * w) + 1;
          const bw = Math.max(1, x(lo + (i + 1) * w) - x(lo + i * w) - 2);
          return (
            <g key={i} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}>
              <rect x={bx - 1} y={M.top} width={bw + 2} height={H - M.bottom - M.top} fill="transparent" />
              <rect className={`bar${hover === i ? " hover" : ""}`} x={bx} y={y(c)} width={bw}
                    height={Math.max(0, y(0) - y(c))} rx={c ? 2 : 0} />
            </g>
          );
        })}
        <line className="baseline" x1={M.left} x2={W - M.right} y1={y(0)} y2={y(0)} />
        {pcts.map(([n, val]) => (
          <g key={n}>
            <line className="marker-line" x1={x(val)} x2={x(val)} y1={M.top} y2={H - M.bottom} />
            <text className="marker-label" x={x(val) + 3} y={M.top + 10}>{n} {fmt(val)}</text>
          </g>
        ))}
        {(() => { const xt = niceTicks(lo, hi, 6); const d = tickDigits(xt);
          return xt.map((t) => <text key={t} x={x(t)} y={H - 8} textAnchor="middle">{fmt(t, d)}</text>); })()}
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ left: `calc(${Math.min((x(lo + hover * w) / W) * 100, 75)}% + 8px)`, top: 34 }}>
          <div className="v">{counts[hover]} runs</div>
          <div className="k">{fmt(lo + hover * w)}–{fmt(lo + (hover + 1) * w)} {unit}</div>
        </div>
      )}
    </div>
  );
}
