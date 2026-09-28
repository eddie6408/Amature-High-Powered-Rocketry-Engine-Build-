import { useMemo, useRef, useState } from "react";

import type { LandingEstimate } from "../station";
import { fmt, linear, niceTicks } from "./scale";

interface Props {
  track: Array<{ e: number; n: number; t: number; alt: number }>;
  estimate: LandingEstimate | null;
  estimateNote: string;
  gnssLabel: string;
  gnssSigma: number;
}

const S = 360;
const PAD = 30;

/** Local east/north plot around the pad (no map tiles: works offline at the field). */
export function TrackMap({ track, estimate, estimateNote, gnssLabel, gnssSigma }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const ref = useRef<SVGSVGElement>(null);
  const { x, y, ticks } = useMemo(() => {
    const es = [0, ...track.map((p) => p.e)];
    const ns = [0, ...track.map((p) => p.n)];
    if (estimate) {
      es.push(estimate.eastM - estimate.radiusM, estimate.eastM + estimate.radiusM);
      ns.push(estimate.northM - estimate.radiusM, estimate.northM + estimate.radiusM);
    }
    const cx = (Math.min(...es) + Math.max(...es)) / 2;
    const cy = (Math.min(...ns) + Math.max(...ns)) / 2;
    const half = Math.max(50, (Math.max(Math.max(...es) - Math.min(...es), Math.max(...ns) - Math.min(...ns)) / 2) * 1.1);
    return {
      x: linear(cx - half, cx + half, PAD, S - 8),
      y: linear(cy - half, cy + half, S - PAD, 8),
      ticks: { e: niceTicks(cx - half, cx + half, 4), n: niceTicks(cy - half, cy + half, 4) },
    };
  }, [track, estimate]);

  // never display more precision than the fix supports
  const step = gnssSigma > 5 ? 10 : 1;
  const r = (v: number) => fmt(Math.round(v / step) * step);
  const now = track.length ? track[track.length - 1] : null;
  const scale = (x(1) - x(0));

  const onMove = (ev: React.PointerEvent<SVGSVGElement>) => {
    if (!track.length || !ref.current) return;
    const b = ref.current.getBoundingClientRect();
    const px = ((ev.clientX - b.left) / b.width) * S;
    const py = ((ev.clientY - b.top) / b.height) * S;
    let best = 0;
    let bd = Infinity;
    track.forEach((p, i) => {
      const d = (x(p.e) - px) ** 2 + (y(p.n) - py) ** 2;
      if (d < bd) { bd = d; best = i; }
    });
    setHover(bd < 24 * 24 ? best : null);
  };
  const hp = hover !== null ? track[hover] : null;

  return (
    <div className="card">
      <h2>Ground track <span className="unit">(m from pad · GPS {gnssLabel}{Number.isFinite(gnssSigma) ? `, ±${gnssSigma} m` : ""})</span></h2>
      {!track.length ? (
        <div className="empty">No GNSS fix yet</div>
      ) : (
        <svg ref={ref} viewBox={`0 0 ${S} ${S}`} width="100%" role="img"
             aria-label="Ground track: launch pad, vehicle track, current position and landing estimate"
             onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
          {ticks.e.map((v) => (
            <g key={`e${v}`}>
              <line className="gridline" x1={x(v)} x2={x(v)} y1={8} y2={S - PAD} />
              <text x={x(v)} y={S - PAD + 14} textAnchor="middle">{fmt(v)}</text>
            </g>
          ))}
          {ticks.n.map((v) => (
            <g key={`n${v}`}>
              <line className="gridline" x1={PAD} x2={S - 8} y1={y(v)} y2={y(v)} />
              <text x={PAD - 4} y={y(v) + 4} textAnchor="end">{fmt(v)}</text>
            </g>
          ))}
          <text x={S - 10} y={S - 4} textAnchor="end">E →</text>
          <text x={PAD + 2} y={18}>N ↑</text>
          {estimate && (
            <g>
              <circle className="track-est" cx={x(estimate.eastM)} cy={y(estimate.northM)}
                      r={Math.max(estimate.radiusM * scale, 4)} />
              <circle className="track-est-center" cx={x(estimate.eastM)} cy={y(estimate.northM)} r={2.5} />
            </g>
          )}
          <polyline className="track-line" points={track.map((p) => `${x(p.e)},${y(p.n)}`).join(" ")} />
          <path className="track-pad" d={`M${x(0)},${y(0) - 7}l6,11h-12z`} />
          {now && <circle className="track-now" cx={x(now.e)} cy={y(now.n)} r={5} />}
          {hp && <circle className="dot" cx={x(hp.e)} cy={y(hp.n)} r={4} />}
        </svg>
      )}
      {hp && (
        <div className="tooltip" style={{ right: 16, top: 34 }}>
          <div className="v">{r(hp.e)} m E, {r(hp.n)} m N</div>
          <div className="k">alt {fmt(hp.alt)} m · t = {fmt(hp.t, 1)} s</div>
        </div>
      )}
      <div className="note">
        ▲ pad · ● vehicle{now ? ` (${r(now.e)} m E, ${r(now.n)} m N)` : ""}
        {estimate && (
          <> · ◌ est. landing {r(estimate.eastM)} m E, {r(estimate.northM)} m N ±{fmt(estimate.radiusM)} m,
            {" "}{fmt(estimate.timeToGroundS)} s — ESTIMATED ({estimate.basis})</>
        )}
        {estimateNote && <> · {estimateNote}</>}
      </div>
    </div>
  );
}
