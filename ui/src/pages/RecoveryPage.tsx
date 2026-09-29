import { useEffect, useState } from "react";

import { api, type Mission } from "../api";
import { fmt } from "../components/scale";
import { Card, ErrorBox, KindBadge, NumberField, SelectField, TextField } from "../components/ui";
import { go } from "../router";

interface SizeOption { diameter_in: number; diameter_m: number; rate_mps: number; rate_fps: number; section_energy_ftlbf: number[];
  max_section_energy_ftlbf: number; recommended: boolean }
interface SizeResult { density: number; required_diameter_m: number; required_diameter_in: number; options: SizeOption[];
  sections_kg: number[]; notes: string[]; canopy_cd: Record<string, number> }
interface DriftCell { wind_mps: number; drift_m: number; descent_s: number }
interface DriftResult { apogee_agl_m: number; winds_mps: number[]; rows: Array<{ main_altitude_m: number | null; cells: DriftCell[] }>;
  has_main: boolean; mass_kg: number; field_radius_m: number | null; note: string;
  devices: Array<{ name: string; diameter_m: number; cd: number; deploy_event: string; deploy_altitude_agl: number | null }> }

const CANOPIES: Array<[string, number]> = [["round / hemispherical", 1.5], ["elliptical", 1.5], ["toroidal", 2.2], ["cruciform (X-form)", 0.8], ["flat sheet", 0.75]];
const nums = (s: string, zeroOk = false) => s.split(/[,\s]+/).filter(Boolean).map(Number).filter((x) => Number.isFinite(x) && (zeroOk ? x >= 0 : x > 0));
const MPH = 0.44704;

export function RecoveryPage({ missionId }: { missionId?: string }) {
  const [missions, setMissions] = useState<Mission[]>([]);
  useEffect(() => { api.get<Mission[]>("/api/missions").then((m) => { setMissions(m); if (!missionId && m.length) go("recovery", m[0].id); }); }, []);
  const mission = missions.find((m) => m.id === missionId);
  return (
    <div className="page stack">
      <div className="dash-row even">
        <Sizing site={mission?.site} />
        <Card title="Why these numbers">
          <ul>
            <li>Steady descent: weight equals drag, v = √(2mg / ρ·Cd·A). Thinner air at high sites means faster descent.</li>
            <li>Mains are usually sized for 4.5–6 m/s (15–20 ft/s). Drogues are usually sized for 20–30 m/s (65–100 ft/s).</li>
            <li>Landing energy is ½mv² for each section hanging from its own line. A common guideline is 75 ft·lbf or less per section.</li>
            <li>Use the Cd your canopy maker publishes on its nominal diameter. The typical values here are estimates.</li>
          </ul>
        </Card>
      </div>
      <Drift missions={missions} missionId={missionId} />
    </div>
  );
}

function Sizing({ site }: { site?: Record<string, number> }) {
  const [mass, setMass] = useState<number | null>(1.4);
  const [rate, setRate] = useState<number | null>(5.5);
  const [canopy, setCanopy] = useState("round / hemispherical");
  const [cd, setCd] = useState<number | null>(1.5);
  const [alt, setAlt] = useState<number | null>(null);
  const [temp, setTemp] = useState<number | null>(null);
  const [sections, setSections] = useState("0.6, 0.8");
  const [res, setRes] = useState<SizeResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (site && alt === null) setAlt(site.altitude_msl ?? 0); }, [site]);
  const run = async () => {
    setErr(null);
    try {
      setRes(await api.post<SizeResult>("/api/recovery/size", { mass_kg: mass, target_rate_mps: rate, cd, altitude_msl: alt ?? 0,
        temperature_c: temp, sections_kg: nums(sections) }));
    } catch (e) { setErr((e as Error).message); }
  };
  useEffect(() => { if (mass && rate && cd) run(); }, [mass, rate, cd, alt, temp, sections]);
  return (
    <Card title="Parachute sizing">
      <div className="presets">
        <button onClick={() => setRate(5.5)}>Main · 5.5 m/s (18 ft/s)</button>
        <button onClick={() => setRate(25)}>Drogue · 25 m/s (82 ft/s)</button>
      </div>
      <div className="form grid-form">
        <NumberField label="Descending mass (motor spent)" unit="kg" value={mass} onChange={setMass} />
        <NumberField label="Target descent rate" unit="m/s" value={rate} onChange={setRate} />
        <SelectField label="Canopy type" value={canopy} onChange={(v) => { setCanopy(v); setCd(CANOPIES.find(([k]) => k === v)?.[1] ?? 1.5); }}
                     options={CANOPIES.map(([k, c]) => [k, `${k} (Cd ≈ ${c})`] as [string, string])} />
        <NumberField label="Cd (maker's figure)" value={cd} onChange={setCd} />
        <NumberField label="Site altitude (above sea level)" unit="m" optional value={alt} onChange={setAlt} />
        <NumberField label="Temperature (blank = standard)" unit="°C" optional value={temp} onChange={setTemp} />
        <TextField label="Section masses under canopy (kg, comma-separated)" value={sections} onChange={setSections} />
      </div>
      <ErrorBox error={err} />
      {res && (
        <>
          <div className="big-answer">
            <span>Needed</span><strong>{fmt(res.required_diameter_m, 2)} m</strong><span className="muted">({fmt(res.required_diameter_in, 1)} in nominal)</span>
            <span className="muted">· air density {fmt(res.density, 3)} kg/m³</span>
          </div>
          <table>
            <thead><tr><th>Standard size</th><th>Descent</th><th>Max section energy</th><th /></tr></thead>
            <tbody>
              {res.options.map((o) => {
                const ok = o.max_section_energy_ftlbf <= 75;
                return (
                  <tr key={o.diameter_in} className={o.recommended ? "selected" : ""}>
                    <td>{o.diameter_in}" <span className="muted">({fmt(o.diameter_m, 2)} m)</span></td>
                    <td>{fmt(o.rate_mps, 1)} m/s <span className="muted">({fmt(o.rate_fps)} ft/s)</span></td>
                    <td className={ok ? "pos" : "neg"}>{ok ? "✓" : "!"} {fmt(o.max_section_energy_ftlbf)} ft·lbf</td>
                    <td>{o.recommended && <span className="state">RECOMMENDED</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="note"><KindBadge kind="ESTIMATED" /> {res.notes.join(" ")}</div>
        </>
      )}
    </Card>
  );
}

function Drift({ missions, missionId }: { missions: Mission[]; missionId?: string }) {
  const [alts, setAlts] = useState("150, 250, 400");
  const [winds, setWinds] = useState("0, 2.5, 5, 7.5, 8.9");
  const [d, setD] = useState<DriftResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    if (!missionId) return;
    setErr(null); setBusy(true);
    try { setD(await api.post<DriftResult>(`/api/missions/${missionId}/recovery-drift`, { main_altitudes: nums(alts), winds: nums(winds, true) })); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  useEffect(() => { setD(null); }, [missionId]);
  const field = d?.field_radius_m ?? null;
  return (
    <Card title={<>Drift and descent <KindBadge kind="SIMULATED" /></>}>
      <div className="form grid-form">
        <SelectField label="Mission" value={missionId ?? ""} onChange={(v) => go("recovery", v)}
                     options={missions.map((m) => [m.id, `${m.name} · ${m.vehicle_id} ${m.revision}`] as [string, string])} />
        <TextField label="Main deploy altitudes (m AGL)" value={alts} onChange={setAlts} />
        <TextField label="Surface winds (m/s)" value={winds} onChange={setWinds} />
        <div className="field"><span>&nbsp;</span><button className="primary" onClick={run} disabled={busy || !missionId}>{busy ? "Simulating…" : "Compute drift"}</button></div>
      </div>
      <ErrorBox error={err} />
      {d && (
        <>
          <div className="note">From apogee {fmt(d.apogee_agl_m)} m AGL, {fmt(d.mass_kg, 2)} kg descending, with{" "}
            {d.devices.map((x) => `${x.name} ${fmt(x.diameter_m * 100)} cm at ${x.deploy_event === "apogee" ? "apogee" : `${fmt(x.deploy_altitude_agl ?? 0)} m`}`).join(", ")}.
            {field ? ` Field radius ${fmt(field)} m.` : " Set a field radius (Launch sites or mission limits) to flag drifts outside it."}
            {!d.has_main && " Single-deploy design: the altitude rows don't apply."}</div>
          <div className="table-scroll">
            <table className="drift">
              <thead><tr><th>Main at</th>{d.winds_mps.map((w) => <th key={w}>{fmt(w, 1)} m/s<br /><span className="muted">{fmt(w / MPH)} mph</span></th>)}</tr></thead>
              <tbody>
                {d.rows.map((r) => (
                  <tr key={String(r.main_altitude_m)}>
                    <td>{r.main_altitude_m === null ? "—" : `${fmt(r.main_altitude_m)} m`}</td>
                    {r.cells.map((c) => {
                      const cls = field === null ? "" : c.drift_m <= field * 0.8 ? "in" : c.drift_m <= field ? "edge" : "out";
                      const icon = cls === "in" ? "✓ " : cls === "edge" ? "! " : cls === "out" ? "✕ " : "";
                      return <td key={c.wind_mps} className={`cell ${cls}`}><strong>{icon}{fmt(c.drift_m)} m</strong><span>{fmt(c.descent_s)} s</span></td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="note">{d.note}</div>
        </>
      )}
    </Card>
  );
}
