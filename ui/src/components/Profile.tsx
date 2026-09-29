import { fmt, linear } from "./scale";

export interface Shape { kind: "body" | "fin" | "motor" | "mass" | "internal"; name: string; points: number[][] }

/** Side view of the vehicle with CG (loaded / burnout) and CP markers. */
export function Profile({ shapes, cg, cgSpent, cp, length }: {
  shapes: Shape[]; cg?: number; cgSpent?: number; cp?: number; length: number }) {
  const W = 900;
  const maxR = Math.max(0.02, ...shapes.flatMap((s) => s.points.map((p) => Math.abs(p[1]))));
  const scale = Math.min((W - 40) / Math.max(length, 0.1), 110 / maxR);
  const H = Math.max(140, 2 * maxR * scale + 60);
  const x = linear(0, 1, 20, 20 + scale);
  const y = (v: number) => H / 2 - v * scale;
  const order = ["internal", "body", "fin", "motor", "mass"];
  const sorted = [...shapes].sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));
  const close = cg !== undefined && cp !== undefined && Math.abs(x(cg) - x(cp)) < 110;
  const anchor = (cls: string) => (!close ? "middle" : cls === "mark-cp" ? ((cp ?? 0) >= (cg ?? 0) ? "start" : "end")
    : cls === "mark-cg" ? ((cp ?? 0) >= (cg ?? 0) ? "end" : "start") : "middle");
  const mark = (pos: number | undefined, label: string, cls: string, dy: number) =>
    pos === undefined ? null : (
      <g className={cls}>
        <line x1={x(pos)} x2={x(pos)} y1={16} y2={H - 16} />
        <text x={x(pos) + (anchor(cls) === "start" ? 4 : anchor(cls) === "end" ? -4 : 0)} y={dy}
              textAnchor={anchor(cls)}>{label} {fmt(pos, 3)} m</text>
      </g>
    );
  return (
    <svg className="profile" viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Vehicle side profile">
      <line className="axis-line" x1={x(0) - 10} x2={x(length) + 10} y1={H / 2} y2={H / 2} />
      {sorted.map((s, i) => (
        <polygon key={i} className={`shape-${s.kind}`} points={s.points.map((p) => `${x(p[0])},${y(p[1])}`).join(" ")}>
          <title>{s.name}</title>
        </polygon>
      ))}
      {mark(cg, "CG", "mark-cg", 12)}
      {cgSpent !== undefined && Math.abs((cgSpent ?? 0) - (cg ?? 0)) > 0.002 && mark(cgSpent, "CG burnout", "mark-cg2", H - 4)}
      {mark(cp, "CP", "mark-cp", 12 + 14)}
    </svg>
  );
}
