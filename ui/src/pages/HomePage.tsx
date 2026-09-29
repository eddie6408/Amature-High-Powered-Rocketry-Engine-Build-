import { useEffect, useState } from "react";

import { api } from "../api";
import { Icon } from "../components/icons";
import { fmt } from "../components/scale";
import { Card } from "../components/ui";
import { go } from "../router";

interface RunRef { id: string; created: string; summary: Record<string, number | string> | null; current: boolean }
interface MissionRow { id: string; name: string; vehicle_id: string; revision: string; motor: string; state: string; ceiling_m: number | null;
  latest: Partial<Record<"simulation" | "montecarlo" | "sil" | "readiness", RunRef>> }
interface FlightRow { flight_id: string; date: string; vehicle_id: string; revision: string; motor: string; status: string;
  apogee_measured_m: number | null; apogee_predicted_m: number | null; apogee_error_pct: number | null; raw_files: number }
interface Dash {
  workspace: { name: string; vehicles: number; flown_revisions: number; motors: number; missions: number; flights: number };
  missions: MissionRow[]; flights: FlightRow[]; checks: Record<string, number>;
  recent_runs: Array<{ id: string; kind: string; mission_id: string; created: string; summary: Record<string, unknown> | null }>;
  totals: { flights: number; analysed: number; mean_abs_apogee_error_pct: number | null; calibrations: number; missions_go: number };
}

const STATE_CLASS: Record<string, string> = { GO: "", "GO WITH WARNINGS": "warn", "NO-GO": "bad", STALE: "warn", "NOT REVIEWED": "idle" };
const when = (iso: string) => new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

export function HomePage() {
  const [d, setD] = useState<Dash | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.get<Dash>("/api/dashboard").then(setD).catch((e) => setErr((e as Error).message)); }, []);
  if (err) return <div className="page"><div className="error">{err}</div></div>;
  if (!d) return <div className="page"><div className="empty">Loading…</div></div>;
  const t = d.totals, w = d.workspace;
  const errPct = t.mean_abs_apogee_error_pct;
  return (
    <div className="page stack dash">
      <section className="kpis">
        <Kpi label="Vehicles" value={String(w.vehicles)} sub={`${w.flown_revisions} flown revision${w.flown_revisions === 1 ? "" : "s"}`} />
        <Kpi label="Missions" value={String(w.missions)} sub={`${t.missions_go} GO on the current design`} />
        <Kpi label="Flights" value={String(w.flights)} sub={`${t.analysed} analysed against prediction`} />
        <Kpi label="Apogee prediction error" value={errPct === null ? "—" : `±${fmt(errPct, 1)}%`}
             sub={errPct === null ? "no analysed flights yet" : `mean of ${t.analysed} flight${t.analysed === 1 ? "" : "s"}`}
             tone={errPct === null ? "" : errPct <= 5 ? "pos" : errPct > 15 ? "neg" : ""} />
        <Kpi label="Motor datasets" value={String(w.motors)} sub={`${t.calibrations} drag calibration${t.calibrations === 1 ? "" : "s"} adopted`} />
      </section>

      {d.missions.length > 0 && (
        <section className="mission-cards">
          {d.missions.map((m) => <MissionCard key={m.id} m={m} />)}
        </section>
      )}

      <div className="dash-row">
        <Card title="Apogee: predicted and measured" className="chart-card">
          <div className="card-sub">Each analysed flight, the prediction for exactly what flew against the reconstructed flight.</div>
          <ApogeeChart flights={d.flights.filter((f) => f.apogee_measured_m !== null && f.apogee_predicted_m !== null)} />
        </Card>
        <Card title="Readiness checks">
          <div className="card-sub">Latest review of each mission on its current design.</div>
          <ChecksDonut tally={d.checks} />
        </Card>
      </div>

      <div className="dash-row">
        <Card title="Recent flights" actions={<button className="link" onClick={() => go("flights")}>View all →</button>}>
          {d.flights.length === 0 ? <div className="empty">No flights yet. Create one in Analyse.</div> : (
            <table>
              <thead><tr><th>Flight</th><th>Date</th><th>Vehicle</th><th>Motor</th><th>Apogee</th><th>Error</th><th>Status</th></tr></thead>
              <tbody>
                {[...d.flights].reverse().slice(0, 8).map((f) => (
                  <tr key={f.flight_id} onClick={() => go("flights", f.flight_id)} className="clickable">
                    <td className="mono">{f.flight_id}</td><td>{f.date || "—"}</td><td>{f.vehicle_id} {f.revision}</td><td>{f.motor}</td>
                    <td>{f.apogee_measured_m === null ? "—" : `${fmt(f.apogee_measured_m)} m`}</td>
                    <td className={f.apogee_error_pct === null ? "" : Math.abs(f.apogee_error_pct) > 10 ? "neg" : "pos"}>
                      {f.apogee_error_pct === null ? "—" : `${f.apogee_error_pct > 0 ? "+" : ""}${fmt(f.apogee_error_pct, 1)}%`}</td>
                    <td><span className={`state ${f.status === "VALIDATED" ? "" : f.status === "NOT ANALYSED" ? "idle" : "warn"}`}>{f.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Recent activity">
          {d.recent_runs.length === 0 ? <div className="empty">Nothing run yet.</div> : (
            <ul className="activity">
              {d.recent_runs.map((r) => (
                <li key={r.id}><span className={`dot k-${r.kind}`} /><span>{runLabel(r.kind, r.summary)}</span>
                  <span className="muted">{r.mission_id} · {when(r.created)}</span></li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <details className="card workflow">
        <summary>Workflow: from idea to flight and back</summary>
        <ol className="steps">
          {[["design", "Design the vehicle", "Components or OpenRocket import; weigh parts as you build."],
            ["motors", "Load real motor data", "ThrustCurve.org search or certified .eng files."],
            ["missions", "Predict the flight", "6-DOF, Monte Carlo, launch simulator with weather."],
            ["readiness", "Test before building", "Flight-software fault suite and GO / NO-GO."],
            ["launchday", "Launch day", "Safety code, flight card, checklist."],
            ["ground", "Fly it", "Ground station with live telemetry."],
            ["flights", "Learn from it", "Reconstruct, compare, calibrate, revise."]].map(([id, t2, dsc]) => (
            <li key={id}><a href={`#/${id}`}><strong>{t2}</strong></a><p>{dsc}</p></li>
          ))}
        </ol>
      </details>
    </div>
  );
}

function runLabel(kind: string, s: Record<string, unknown> | null): string {
  const n = (k: string) => (typeof s?.[k] === "number" ? (s[k] as number) : null);
  if (kind === "simulation") return `Simulation · apogee ${fmt(n("apogee_agl_m") ?? 0)} m`;
  if (kind === "montecarlo") return `Monte Carlo · P50 ${fmt(n("apogee_p50") ?? 0)} m, P95 ${fmt(n("apogee_p95") ?? 0)} m`;
  if (kind === "sil") return `Fault suite · ${n("passed") ?? "?"}/${n("total") ?? "?"} scenarios`;
  if (kind === "readiness") return `Readiness · ${String(s?.status ?? "?")}`;
  return kind;
}

function Kpi({ label, value, sub, tone = "" }: { label: string; value: string; sub: string; tone?: string }) {
  return <div className="tile kpi"><div className="label">{label}</div><div className={`value ${tone}`}>{value}</div><div className="sub">{sub}</div></div>;
}

function MissionCard({ m }: { m: MissionRow }) {
  const sim = m.latest.simulation, mc = m.latest.montecarlo, sil = m.latest.sil;
  const stale = (r?: RunRef) => (r && !r.current ? " (stale)" : "");
  const num = (r: RunRef | undefined, k: string) => (r?.summary && typeof r.summary[k] === "number" ? (r.summary[k] as number) : null);
  return (
    <div className="card mission-card">
      <div className="mc-head">
        <span className="mc-icon"><Icon name="launch" size={18} /></span>
        <div><strong>{m.name}</strong><small>{m.vehicle_id} {m.revision} · {m.motor}</small></div>
        <span className={`state ${STATE_CLASS[m.state] ?? "idle"}`}>{m.state}</span>
      </div>
      <div className="mc-stats">
        <div><small>Predicted apogee</small><b>{num(sim, "apogee_agl_m") === null ? "—" : `${fmt(num(sim, "apogee_agl_m")!)} m`}</b>
          <span>{sim ? `simulated${stale(sim)}` : "not simulated"}</span></div>
        <div><small>Monte Carlo P95</small><b>{num(mc, "apogee_p95") === null ? "—" : `${fmt(num(mc, "apogee_p95")!)} m`}</b>
          <span>{mc ? `${num(mc, "runs") ?? "?"} runs${stale(mc)}` : "not run"}</span></div>
        <div><small>Fault suite</small><b>{sil ? `${num(sil, "passed")}/${num(sil, "total")}` : "—"}</b>
          <span>{sil ? `scenarios pass${stale(sil)}` : "not run"}</span></div>
      </div>
      <div className="mc-foot">
        <span className="muted">{m.ceiling_m ? `Waiver ceiling ${fmt(m.ceiling_m)} m` : "No waiver ceiling set"}</span>
        <div className="mc-actions">
          <button onClick={() => go("missions", m.id)}>Simulate</button>
          <button onClick={() => go("launch", m.id)}>Launch sim</button>
          <button className="primary" onClick={() => go("launchday", m.id)}>Launch day</button>
        </div>
      </div>
    </div>
  );
}

function ApogeeChart({ flights }: { flights: FlightRow[] }) {
  if (flights.length === 0) return <div className="empty">No analysed flights yet. Analyse a flight to see prediction against reality.</div>;
  const W = 640, H = 240, L = 56, R = 16, T = 16, B = 34;
  const vals = flights.flatMap((f) => [f.apogee_measured_m!, f.apogee_predicted_m!]);
  const hi = Math.max(...vals) * 1.12, lo = 0;
  const step = [50, 100, 200, 250, 500, 1000, 2000, 5000].find((s) => hi / s <= 5) ?? 10000;
  const y = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const band = (W - L - R) / flights.length;
  const x = (i: number) => L + band * (i + 0.5);
  const ticks = Array.from({ length: Math.floor(hi / step) + 1 }, (_, i) => i * step);
  return (
    <div>
      <div className="legend"><span><i className="key" />Predicted</span><span><i className="key s2" />Measured</span></div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Predicted and measured apogee per flight">
        {ticks.map((v) => <g key={v}><line className="gridline" x1={L} x2={W - R} y1={y(v)} y2={y(v)} /><text x={L - 8} y={y(v) + 4} textAnchor="end">{fmt(v)} m</text></g>)}
        {flights.map((f, i) => {
          const bw = Math.min(28, band * 0.28);
          return (
            <g key={f.flight_id}>
              <rect className="bar" x={x(i) - bw - 2} y={y(f.apogee_predicted_m!)} width={bw} height={y(0) - y(f.apogee_predicted_m!)} rx={3} />
              <rect className="bar s2" x={x(i) + 2} y={y(f.apogee_measured_m!)} width={bw} height={y(0) - y(f.apogee_measured_m!)} rx={3} />
              <text className="direct-label" x={x(i)} y={Math.min(y(f.apogee_predicted_m!), y(f.apogee_measured_m!)) - 6} textAnchor="middle">
                {f.apogee_error_pct! > 0 ? "+" : ""}{fmt(f.apogee_error_pct!, 1)}%</text>
              <text x={x(i)} y={H - 12} textAnchor="middle">{f.flight_id}</text>
            </g>
          );
        })}
        <line className="baseline" x1={L} x2={W - R} y1={y(0)} y2={y(0)} />
      </svg>
    </div>
  );
}

function ChecksDonut({ tally }: { tally: Record<string, number> }) {
  const parts: Array<[string, string, number]> = [["PASS", "var(--good)", tally.PASS ?? 0], ["WARN", "var(--warning)", tally.WARN ?? 0],
    ["FAIL", "var(--critical)", tally.FAIL ?? 0], ["NOT RUN", "var(--text-muted)", tally["NOT RUN"] ?? 0]];
  const total = parts.reduce((a, [, , n]) => a + n, 0);
  if (total === 0) return <div className="empty">No current readiness review. Run one on the Test &amp; readiness page.</div>;
  const r = 70, C = 2 * Math.PI * r;
  let off = 0;
  return (
    <div className="donut">
      <svg viewBox="0 0 180 180" width="180" height="180" role="img" aria-label="Readiness check results">
        <g transform="translate(90,90) rotate(-90)">
          {parts.filter(([, , n]) => n > 0).map(([k, c, n]) => {
            const len = (n / total) * C;
            const el = <circle key={k} r={r} fill="none" stroke={c} strokeWidth={18} strokeDasharray={`${Math.max(0, len - 2)} ${C}`} strokeDashoffset={-off} />;
            off += len;
            return el;
          })}
        </g>
        <text x="90" y="92" textAnchor="middle" className="donut-big">{total}</text>
        <text x="90" y="112" textAnchor="middle">checks</text>
      </svg>
      <ul>
        {parts.map(([k, c, n]) => (
          <li key={k}><i style={{ background: c }} />{k === "PASS" ? "✓ " : k === "FAIL" ? "✕ " : k === "WARN" ? "! " : "– "}{k}<b>{n}</b>
            <span className="muted">{total ? `${Math.round((100 * n) / total)}%` : ""}</span></li>
        ))}
      </ul>
    </div>
  );
}
