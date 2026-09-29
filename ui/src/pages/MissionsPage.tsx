import { useEffect, useState } from "react";

import { api, waitJob, type Job, type Mission, type Motor, type Summary, type VehicleSummary } from "../api";
import { Dispersion } from "../components/Dispersion";
import { Histogram } from "../components/Histogram";
import { LineChart, type Marker } from "../components/LineChart";
import { fmt } from "../components/scale";
import { Card, ErrorBox, KindBadge, NumberField, Progress, SelectField, Stat, TextField } from "../components/ui";
import { go } from "../router";
import type { Site } from "./SitesPage";

interface SimRun { id: string; created: string; summary: Summary; events: Array<[number, string]>;
  series: Record<string, number[]>; notes: string[]; motor_quality: string;
  stages?: Array<{ stage: number; parallel: boolean; separation_time_s: number; separation_altitude_m: number; separation_speed_mps: number;
    apogee_agl_m: number; landing_east_m: number; landing_north_m: number; impact_speed_mps: number; landing_time_s: number }> }
interface McRun { id: string; created: string; report: { statistics: Record<string, Record<string, number>>;
  landing_dispersion: null | { mean_east_m: number; mean_north_m: number; semi_major_m: number; semi_minor_m: number;
  major_axis_bearing_deg: number; confidence: number; max_range_m: number } }; landing: Array<[number | null, number | null]>;
  apogees: number[]; failures: unknown[]; note: string }

export function MissionsPage({ missionId }: { missionId?: string }) {
  const [missions, setMissions] = useState<Mission[]>([]);
  const load = () => api.get<Mission[]>("/api/missions").then(setMissions);
  useEffect(() => { load(); }, []);
  useEffect(() => { if (!missionId && missions.length) go("missions", missions[0].id); }, [missionId, missions]);
  return (
    <div className="page two-col">
      <aside className="side">
        <Card title="Missions" actions={<button onClick={() => go("missions", "new")}>+ New</button>}>
          <ul className="list">
            {missions.map((m) => (
              <li key={m.id} className={m.id === missionId ? "active" : ""}>
                <a href={`#/missions/${m.id}`}><strong>{m.name}</strong><span className="muted"> · {m.vehicle_id} {m.revision}</span></a>
              </li>
            ))}
          </ul>
        </Card>
      </aside>
      <main>{missionId && <MissionView key={missionId} missionId={missionId} onSaved={load} />}</main>
    </div>
  );
}

const blank = (): Mission => ({
  id: "", name: "New mission", vehicle_id: "", revision: "", motor_key: "",
  site: { altitude_msl: 0, latitude: 0, longitude: 0, rail_length: 2.4, elevation_deg: 87, azimuth_deg: 0 },
  wind: { model: "power_law", speed: 3, from_deg: 270 },
  atmosphere: { temperature_offset: 0, sea_level_pressure: 101325, relative_humidity: 0 },
  limits: { min_margin_cal: 1, max_margin_cal: 4, min_rail_exit_mps: 15, min_thrust_to_weight: 5,
            altitude_ceiling_agl_m: null, field_radius_m: null, max_drogue_rate_mps: 30,
            main_rate_min_mps: 3.5, main_rate_max_mps: 7.6, max_mach_analytical: 0.8 },
  uncertainty: { dry_mass_rel_sigma: 0.02, cg_sigma_m: 0.01, impulse_rel_sigma: 0.03, burn_time_rel_sigma: 0.03,
                 cd_rel_sigma: 0.08, wind_speed_sigma: 1.5, wind_dir_sigma_deg: 30, gust_sigma_max: 1.5,
                 temperature_sigma_k: 5, elevation_sigma_deg: 1, azimuth_sigma_deg: 2 },
});

function WindProfileEditor({ m, set }: { m: Mission; set: (fn: (x: Mission) => void) => void }) {
  const [ref, setRef] = useState("AGL");
  const [msg, setMsg] = useState<string | null>(null);
  const alts = (m.wind.altitudes as number[]) ?? [];
  const spd = (m.wind.speeds as number[]) ?? [];
  const dir = (m.wind.from_deg as unknown as number[]) ?? [];
  const importText = async (text: string) => {
    try {
      const w = await api.post<Record<string, unknown> & { units_detected: Record<string, string> }>("/api/wind/parse",
        { text, altitude_ref: ref, site_altitude_msl: m.site.altitude_msl });
      set((x) => { x.wind = { model: "layered", altitudes: w.altitudes, speeds: w.speeds, from_deg: w.from_deg }; });
      const u = w.units_detected;
      setMsg(`imported ${(w.altitudes as number[]).length} levels (altitude ${u.altitude} ${u.reference}, speed ${u.speed}) - check them below`);
    } catch (e) { setMsg((e as Error).message); }
  };
  return (
    <div className="wind-profile">
      <div className="form grid-form">
        <SelectField label="Imported altitudes are" value={ref} onChange={setRef} options={[["AGL", "above the pad (AGL)"], ["MSL", "above sea level (MSL)"]]} />
        <label className="field"><span>Import forecast / sounding (CSV: altitude, speed, direction)</span>
          <input type="file" accept=".csv,.txt,.tsv" onChange={async (e) => { const f = e.target.files?.[0]; if (f) await importText(await f.text()); e.target.value = ""; }} /></label>
      </div>
      {msg && <div className="note">{msg}</div>}
      <table className="stats">
        <thead><tr><th>Altitude AGL (m)</th><th>Speed (m/s)</th><th>From (°)</th><th></th></tr></thead>
        <tbody>{alts.map((_, i) => (
          <tr key={i}>
            {[alts, spd, dir].map((arr, k) => (
              <td key={k}><input type="number" step="any" value={arr[i]} onChange={(e) => set((x) => {
                const key = (["altitudes", "speeds", "from_deg"] as const)[k];
                (x.wind[key] as number[])[i] = Number(e.target.value);
              })} /></td>))}
            <td><button className="link danger" onClick={() => set((x) => { for (const k of ["altitudes", "speeds", "from_deg"] as const) (x.wind[k] as number[]).splice(i, 1); })}>remove</button></td>
          </tr>))}
        </tbody>
      </table>
      <button onClick={() => set((x) => { const n = alts.length;
        (x.wind.altitudes as number[]).push(n ? alts[n - 1] + 500 : 0); (x.wind.speeds as number[]).push(n ? spd[n - 1] : 3);
        (x.wind.from_deg as unknown as number[]).push(n ? dir[n - 1] : 270); })}>Add level</button>
      <div className="note">Monte Carlo disperses this profile (speed ×N(1, σ), direction ± σ) instead of a generic power law.</div>
    </div>
  );
}

function MissionView({ missionId, onSaved }: { missionId: string; onSaved: () => void }) {
  const [m, setM] = useState<Mission | null>(null);
  const [vehicles, setVehicles] = useState<VehicleSummary[]>([]);
  const [motors, setMotors] = useState<Motor[]>([]);
  const [sim, setSim] = useState<SimRun | null>(null);
  const [mc, setMc] = useState<McRun | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [n, setN] = useState(100);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sites, setSites] = useState<Site[]>([]);

  useEffect(() => {
    api.get<Site[]>("/api/sites").then(setSites).catch(() => undefined);
    api.get<VehicleSummary[]>("/api/vehicles").then(setVehicles);
    api.get<Motor[]>("/api/motors").then(setMotors);
    if (missionId === "new") setM(blank());
    else {
      api.get<Mission>(`/api/missions/${missionId}`).then(setM).catch((e) => setErr(e.message));
      api.get<Array<{ id: string; kind: string }>>(`/api/runs?mission=${missionId}`).then(async (runs) => {
        const s = runs.find((r) => r.kind === "simulation");
        const c = runs.find((r) => r.kind === "montecarlo");
        if (s) setSim(await api.get<SimRun>(`/api/runs/${s.id}`));
        if (c) setMc(await api.get<McRun>(`/api/runs/${c.id}`));
      });
    }
  }, [missionId]);
  if (!m) return <ErrorBox error={err} />;
  const set = (fn: (x: Mission) => void) => { const c = structuredClone(m); fn(c); setM(c); };
  const veh = vehicles.find((v) => v.vehicle_id === m.vehicle_id);

  const save = async (): Promise<string | null> => {
    setErr(null);
    try {
      const id = m.id || m.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "mission";
      const saved = await api.post<Mission>("/api/missions", { ...m, id });
      setM(saved); onSaved();
      if (missionId !== saved.id) go("missions", saved.id);
      return saved.id;
    } catch (e) { setErr((e as Error).message); return null; }
  };
  const simulate = async () => {
    const id = await save(); if (!id) return;
    setBusy(true);
    try { setSim(await api.post<SimRun>(`/api/missions/${id}/simulate`)); } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };
  const runMc = async () => {
    const id = await save(); if (!id) return;
    try {
      const { job: jid } = await api.post<{ job: string }>(`/api/missions/${id}/montecarlo`, { n });
      const j = await waitJob(jid, setJob);
      if (j.status === "error") setErr(j.error);
      else if (j.result_id) setMc(await api.get<McRun>(`/api/runs/${j.result_id}`));
    } catch (e) { setErr((e as Error).message); }
    setJob(null);
  };

  const s = sim?.summary;
  const series = (k: string) => (sim ? sim.series.t.map((t, i) => ({ t, v: sim.series[k][i] })) : []);
  const markers: Marker[] = (sim?.events ?? []).filter(([, e]) => ["burnout", "apogee", "deploy:main", "landing"].includes(e))
    .map(([t, e]) => ({ t, label: e.replace("deploy:", "") }));

  return (
    <div className="stack">
      <div className="toolbar">
        <h1>{m.name}</h1><span className="spacer" />
        <button onClick={save}>Save</button>
        <button className="primary" onClick={simulate} disabled={busy || !m.vehicle_id || !m.motor_key}>{busy ? "Simulating…" : "Simulate"}</button>
      </div>
      <ErrorBox error={err} />
      <Card title="Setup">
        <div className="form grid-form">
          <TextField label="Name" value={m.name} onChange={(v) => set((x) => { x.name = v; })} />
          <SelectField label="Vehicle" value={m.vehicle_id} onChange={(v) => set((x) => {
            x.vehicle_id = v; const vv = vehicles.find((q) => q.vehicle_id === v); x.revision = vv ? vv.revisions[vv.revisions.length - 1].label : ""; })}
                       options={[["", "—"], ...vehicles.map((v) => [v.vehicle_id, `${v.vehicle_id} ${v.name}`] as [string, string])]} />
          <SelectField label="Revision" value={m.revision} onChange={(v) => set((x) => { x.revision = v; })}
                       options={(veh?.revisions ?? []).map((r) => [r.label, `${r.label} (${r.status.toLowerCase()})`] as [string, string])} />
          <SelectField label="Motor" value={m.motor_key} onChange={(v) => set((x) => { x.motor_key = v; })}
                       options={[["", "—"], ...motors.map((q) => [q.key, `${q.manufacturer} ${q.designation} (${q.data_quality.toLowerCase()})`] as [string, string])]} />
        </div>
        <MotorsAndStaging m={m} motors={motors} set={set} />
        <h3>Launch site</h3>
        <div className="form grid-form">
          <SelectField label="From the site library" value={m.site_id ?? ""}
                       onChange={(id) => set((x) => {
                         const st = sites.find((q) => q.id === id);
                         x.site_id = id || undefined;
                         if (!st) return;
                         x.site.latitude = st.latitude; x.site.longitude = st.longitude;
                         if (st.altitude_msl !== undefined) x.site.altitude_msl = st.altitude_msl;
                         if (st.rail_length_m) x.site.rail_length = st.rail_length_m;
                         if (st.waiver_ceiling_agl_m) x.limits.altitude_ceiling_agl_m = st.waiver_ceiling_agl_m;
                         if (st.field_radius_m) x.limits.field_radius_m = st.field_radius_m;
                       })}
                       options={[["", sites.length ? "— (enter by hand)" : "no sites yet: add them in Launch sites"],
                                 ...sites.map((q) => [q.id, q.name] as [string, string])]} />
        </div>
        <div className="form grid-form">
          {([["altitude_msl", "Site altitude (MSL)", "m"], ["latitude", "Latitude", "°"], ["longitude", "Longitude", "°"],
             ["rail_length", "Rail length", "m"], ["elevation_deg", "Rail angle above horizontal", "°"],
             ["azimuth_deg", "Rail azimuth (from north)", "°"]] as const).map(([k, l, u]) => (
            <NumberField key={k} label={l} unit={u} value={m.site[k]} onChange={(v) => set((x) => { x.site[k] = v ?? 0; })} />
          ))}
        </div>
        <h3>Wind &amp; atmosphere</h3>
        <div className="form grid-form">
          <SelectField label="Wind model" value={String(m.wind.model)} onChange={(v) => set((x) => {
            x.wind.model = v;
            if (v === "layered" && !Array.isArray(x.wind.altitudes)) {
              const s = Number(x.wind.speed ?? 3), d = Number(x.wind.from_deg ?? 270);
              x.wind.altitudes = [0, 300, 1000]; x.wind.speeds = [s, s * 1.4, s * 1.8]; x.wind.from_deg = [d, d, d];
            }
          })} options={[["power_law", "Power law (surface ref. 10 m)"], ["constant", "Constant with altitude"],
                        ["layered", "Measured / forecast profile"]]} />
          {m.wind.model !== "layered" && <>
            <NumberField label="Wind speed" unit="m/s" value={m.wind.speed as number} onChange={(v) => set((x) => { x.wind.speed = v ?? 0; })} />
            <NumberField label="Wind from" unit="°" value={m.wind.from_deg as number} onChange={(v) => set((x) => { x.wind.from_deg = v ?? 0; })} />
          </>}
          <NumberField label="Temperature offset from standard" unit="K" value={m.atmosphere.temperature_offset}
                       onChange={(v) => set((x) => { x.atmosphere.temperature_offset = v ?? 0; })} />
          <NumberField label="Sea-level pressure" unit="Pa" value={m.atmosphere.sea_level_pressure}
                       onChange={(v) => set((x) => { x.atmosphere.sea_level_pressure = v ?? 101325; })} />
        </div>
        {m.wind.model === "layered" && <WindProfileEditor m={m} set={set} />}
        <h3>Limits <span className="muted">(set from your safety code, waiver and field)</span></h3>
        <div className="form grid-form">
          {([["min_margin_cal", "Min static margin", "cal"], ["max_margin_cal", "Max static margin", "cal"],
             ["min_rail_exit_mps", "Min rail exit velocity", "m/s"], ["min_thrust_to_weight", "Min thrust-to-weight", ": 1"],
             ["altitude_ceiling_agl_m", "Altitude ceiling (AGL)", "m"], ["field_radius_m", "Recovery field radius", "m"],
             ["max_drogue_rate_mps", "Max drogue descent", "m/s"], ["main_rate_min_mps", "Min landing descent", "m/s"],
             ["main_rate_max_mps", "Max landing descent", "m/s"]] as const).map(([k, l, u]) => (
            <NumberField key={k} label={l} unit={u} optional={k.endsWith("_m")} value={m.limits[k]}
                         onChange={(v) => set((x) => { x.limits[k] = v; })} />
          ))}
        </div>
      </Card>

      {s && (
        <>
          <div className="section-head"><h2>Nominal prediction</h2><KindBadge kind="SIMULATED" />
            {sim && sim.motor_quality !== "CERTIFIED" && sim.motor_quality !== "MANUFACTURER" && sim.motor_quality !== "MEASURED" &&
              <span className="warn-line">motor data is {sim.motor_quality} - not a flight prediction</span>}</div>
          <section className="tiles">
            <Stat label="Apogee (AGL)" value={fmt(Number(s.apogee_agl_m))} unit="m" />
            <Stat label="Max velocity" value={fmt(Number(s.max_velocity_mps))} unit="m/s" sub={`Mach ${fmt(Number(s.max_mach), 2)}`} />
            <Stat label="Max accel" value={fmt(Number(s.max_axial_accel_mps2) / 9.80665, 1)} unit="g" />
            <Stat label="Rail exit" value={fmt(Number(s.rail_exit_velocity_mps), 1)} unit="m/s" />
            <Stat label="Min margin" value={fmt(Number(s.min_stability_margin_cal), 2)} unit="cal" />
            <Stat label="Time to apogee" value={fmt(Number(s.time_to_apogee_s), 1)} unit="s" />
            <Stat label="Flight time" value={s.flight_time_s !== null ? fmt(Number(s.flight_time_s)) : "—"} unit="s" />
            <Stat label="Landing" value={fmt(Math.hypot(Number(s.landing_east_m ?? 0), Number(s.landing_north_m ?? 0)))} unit="m"
                  sub={`${fmt(Number(s.landing_east_m ?? 0))} E, ${fmt(Number(s.landing_north_m ?? 0))} N`} />
          </section>
          {sim?.stages && sim.stages.length > 0 && (
            <Card title={<>Separated stages <KindBadge kind="SIMULATED" /></>}>
              <table>
                <thead><tr><th>Stage</th><th>Separation</th><th>Its apogee</th><th>Lands</th><th>Impact speed</th></tr></thead>
                <tbody>
                  {sim.stages.map((b) => (
                    <tr key={b.stage}>
                      <td>{b.parallel ? "side boosters" : "booster"} (stage {b.stage})</td>
                      <td>T+{fmt(b.separation_time_s, 1)} s at {fmt(b.separation_altitude_m)} m, {fmt(b.separation_speed_mps)} m/s</td>
                      <td>{fmt(b.apogee_agl_m)} m</td>
                      <td>{fmt(Math.hypot(b.landing_east_m, b.landing_north_m))} m from the pad, T+{fmt(b.landing_time_s)} s</td>
                      <td className={b.impact_speed_mps > 10 ? "neg" : ""}>{fmt(b.impact_speed_mps, 1)} m/s</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="note">Separated stages fall as tumbling bodies (drag ESTIMATED); give them their own recovery for a soft landing.</div>
            </Card>
          )}
          {sim?.notes?.some((n) => n.startsWith("several motors")) && <div className="note">{sim.notes.find((n) => n.startsWith("several motors"))}</div>}
          <div className="chart-grid">
            <LineChart title="Altitude" unit="m" data={series("altitude")} markers={markers} />
            <LineChart title="Speed" unit="m/s" digits={1} data={series("speed")} markers={markers} />
            <LineChart title="Axial acceleration" unit="m/s²" digits={1} data={series("axial_accel")} markers={markers} />
            <LineChart title="Static margin, powered flight" unit="cal" digits={2} data={series("margin_cal").filter((p, i) => sim!.series.thrust[i] > 0 || p.t < 3)} />
          </div>
        </>
      )}

      <Card title="Monte Carlo dispersion" actions={
        <>
          <NumberField label="Runs" value={n} step="1" onChange={(v) => setN(Math.max(2, Math.min(5000, v ?? 100)))} />
          <button className="primary" onClick={runMc} disabled={!!job || !m.vehicle_id}>Run</button>
        </>}>
        {job && <Progress done={job.done} total={job.total} label="runs" />}
        {mc && (
          <>
            <div className="note">{mc.note} Run {mc.id}. Failures: {mc.failures.length}.</div>
            <table className="stats">
              <thead><tr><th>Output</th><th>mean</th><th>σ</th><th>P5</th><th>P50</th><th>P95</th></tr></thead>
              <tbody>
                {Object.entries(mc.report.statistics).filter(([, v]) => v && v.n).map(([k, v]) => (
                  <tr key={k}><td>{k}</td>{["mean", "std", "p05", "p50", "p95"].map((c) => <td key={c}>{fmt(v[c], 1)}</td>)}</tr>
                ))}
              </tbody>
            </table>
            <div className="chart-grid">
              <Histogram title="Apogee" unit="m" values={mc.apogees} />
              <Dispersion points={mc.landing} ellipse={mc.report.landing_dispersion} fieldRadius={m.limits.field_radius_m} />
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

/** Clusters, stacked stages, side boosters and airstarts. Empty = the mission's single motor. */
function MotorsAndStaging({ m, motors, set }: { m: Mission; motors: Motor[]; set: (fn: (x: Mission) => void) => void }) {
  const groups = m.motors ?? [];
  const stages = [...new Set(groups.map((g) => g.stage))].filter((s) => s > 0).sort();
  const syncSeps = (x: Mission) => {
    const want = [...new Set((x.motors ?? []).map((g) => g.stage))].filter((s) => s > 0);
    const have = x.separations ?? [];
    x.separations = want.sort().map((st) => have.find((q) => q.stage === st) ?? { stage: st, delay: 0.5, parallel: false });
  };
  const opts = motors.map((q) => [q.key, `${q.manufacturer} ${q.designation}`] as [string, string]);
  return (
    <>
      <h3>Motors &amp; staging <span className="muted">(clusters, stages, side boosters, airstarts)</span></h3>
      {groups.length === 0 ? (
        <div className="note">One motor: the one chosen above. <button className="link" onClick={() => set((x) => {
          x.motors = [{ motor_key: x.motor_key, count: 1, stage: 0, ignition: "launch", delay: 0 }]; syncSeps(x); })}>Set up several motors or stages</button></div>
      ) : (
        <>
          <table className="motor-groups">
            <thead><tr><th>Motor</th><th>Count</th><th>Stage</th><th>Ignition</th><th>Delay (s)</th><th /></tr></thead>
            <tbody>
              {groups.map((g, i) => (
                <tr key={i}>
                  <td><select value={g.motor_key} aria-label="Motor" onChange={(e) => set((x) => { x.motors![i].motor_key = e.target.value; if (i === 0) x.motor_key = e.target.value; })}>
                    {opts.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
                  <td><input type="number" min={1} step={1} value={g.count} aria-label="Count" onChange={(e) => set((x) => { x.motors![i].count = Math.max(1, Number(e.target.value) || 1); })} /></td>
                  <td><input type="number" min={0} step={1} value={g.stage} aria-label="Stage" onChange={(e) => set((x) => { x.motors![i].stage = Math.max(0, Number(e.target.value) || 0); syncSeps(x); })} /></td>
                  <td><select value={g.ignition} aria-label="Ignition" onChange={(e) => set((x) => { x.motors![i].ignition = e.target.value as "launch"; })}>
                    <option value="launch">at launch</option><option value="time">after launch (airstart)</option><option value="separation">after the stage below separates</option></select></td>
                  <td><input type="number" min={0} step="any" value={g.delay} aria-label="Delay" disabled={g.ignition === "launch"}
                             onChange={(e) => set((x) => { x.motors![i].delay = Math.max(0, Number(e.target.value) || 0); })} /></td>
                  <td><button className="link danger" onClick={() => set((x) => { x.motors!.splice(i, 1); if (!x.motors!.length) { x.motors = []; } syncSeps(x); })}>remove</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <button onClick={() => set((x) => { x.motors = [...(x.motors ?? []), { motor_key: x.motor_key, count: 1, stage: 0, ignition: "launch", delay: 0 }]; syncSeps(x); })}>Add motor group</button>
          {stages.length > 0 && (
            <div className="form grid-form seps">
              {(m.separations ?? []).map((sp, i) => (
                <div key={sp.stage} className="field">
                  <span>Stage {sp.stage} separates after burnout +</span>
                  <input type="number" min={0} step="any" value={sp.delay} aria-label={`Separation delay stage ${sp.stage}`}
                         onChange={(e) => set((x) => { x.separations![i].delay = Math.max(0, Number(e.target.value) || 0); })} />
                  <label className="check"><input type="checkbox" checked={sp.parallel} onChange={(e) => set((x) => { x.separations![i].parallel = e.target.checked; })} />
                    side boosters (not stacked)</label>
                </div>
              ))}
            </div>
          )}
          <div className="note">Stage 0 is the top stage (sustainer, or the core with side boosters); stage 1 is below it or the side
            boosters. The design's components carry their stage (OpenRocket imports set it). Each stage's motors sit in that
            stage's motor slot. Separated stages are flown to the ground as tumbling bodies (drag ESTIMATED).</div>
        </>
      )}
    </>
  );
}
