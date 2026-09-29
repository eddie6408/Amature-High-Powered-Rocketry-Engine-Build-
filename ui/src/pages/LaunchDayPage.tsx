import { useEffect, useState } from "react";

import { api, type Mission } from "../api";
import { fmt } from "../components/scale";
import { Card, ErrorBox, KindBadge, NumberField, SelectField, StatusPill, TextField } from "../components/ui";
import { go } from "../router";

interface Profile { name?: string; organization?: string; member_number?: string; cert_level?: number; cert_org?: string }
interface SafetyItem { id: string; name: string; status: string; value: string; requirement: string; source: string }
interface FlightCard {
  mission: { id: string; name: string; site: Record<string, number> };
  flyer: Profile;
  vehicle: { id: string; revision: string; name: string; length_m: number; diameter_m: number; liftoff_mass_kg: number; cg_m: number;
    cp_m: number; margin_cal: number; mass_kind: string };
  motor: { designation: string; manufacturer: string; class: string; total_impulse_ns: number; average_thrust_n: number; burn_time_s: number;
    data_quality: string; delays: string };
  prediction: { apogee_agl_m: number; apogee_p95_m: number | null; max_speed_mps: number; max_mach: number; rail_exit_mps: number;
    time_to_apogee_s: number; landing_distance_m: number; landing_rate_mps: number | null };
  recovery: Array<{ name: string; diameter_m: number; cd: number; deploy: string }>;
  flutter: Array<{ fin: string; min_ratio: number; flutter_speed_mps: number; speed_mps: number; altitude_agl_m: number; shear_modulus_source: string }>;
  safety: { status: string; items: SafetyItem[]; required_cert: { level: number; label: string }; note: string };
}
interface Conditions { wind_speed: number | null; gust_speed: number | null; cloud_cover_pct: number | null; visibility_m: number | null; complex_rocket: boolean }

const FT = 0.3048;
const CHECKLIST: Array<[string, string[]]> = [
  ["Recovery", ["Parachutes packed; shock cords and quick links attached and closed", "Chute protector / Nomex in place",
    "Shear pins installed; sections fit without binding", "Deployment settings match the flight card (main altitude)"]],
  ["Avionics", ["Batteries fresh or fully charged", "Altimeter configuration verified", "Arming switches OFF for transport",
    "Tracker on; position received at the ground station"]],
  ["Airframe & motor", ["Motor installed per the manufacturer's instructions and positively retained",
    "Loaded CG marked and matches the flight card", "Rail buttons aligned; rocket slides freely on the rail", "Fins and airframe undamaged"]],
  ["At the pad", ["RSO inspection passed; flight card signed", "Rocket on the rail; launcher angle set",
    "Altimeters armed on the pad; continuity confirmed", "Igniter installed at the pad; controller continuity confirmed", "Range and sky clear"]],
  ["After the flight", ["Rocket located and recovered safely", "Flight data downloaded", "Flight logged in Analyse (raw files attached)"]],
];

function loadChecks(key: string): Record<string, boolean> {
  try { return JSON.parse(localStorage.getItem(key) ?? "{}"); } catch { return {}; }
}

export function LaunchDayPage({ missionId }: { missionId?: string }) {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [profile, setProfile] = useState<Profile>({});
  const [saved, setSaved] = useState<string | null>(null);
  const [cond, setCond] = useState<Conditions>({ wind_speed: null, gust_speed: null, cloud_cover_pct: null, visibility_m: null, complex_rocket: false });
  const [wxNote, setWxNote] = useState<string | null>(null);
  const [card, setCard] = useState<FlightCard | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ckKey = `aerodyne-checklist-${missionId ?? ""}`;
  const [checks, setChecks] = useState<Record<string, boolean>>(() => loadChecks(ckKey));

  useEffect(() => {
    api.get<Mission[]>("/api/missions").then((m) => { setMissions(m); if (!missionId && m.length) go("launchday", m[0].id); });
    api.get<Profile>("/api/profile").then(setProfile).catch(() => undefined);
  }, []);
  useEffect(() => { setCard(null); setChecks(loadChecks(ckKey)); }, [missionId]);
  const mission = missions.find((m) => m.id === missionId);

  const saveProfile = async () => {
    setErr(null);
    try { setProfile(await api.put<Profile>("/api/profile", profile)); setSaved("Saved"); setTimeout(() => setSaved(null), 1500); }
    catch (e) { setErr((e as Error).message); }
  };
  const liveWeather = async () => {
    if (!mission) return;
    setWxNote("Loading live weather…");
    try {
      const lw = await api.get<{ weather: { wind_speed: number }; peak_gust: number | null; cloud_cover_pct: number | null; visibility_m: number | null;
        observed_at: string; source: string }>(`/api/weather/live?lat=${mission.site.latitude}&lon=${mission.site.longitude}`);
      setCond((c) => ({ ...c, wind_speed: lw.weather.wind_speed, gust_speed: lw.peak_gust, cloud_cover_pct: lw.cloud_cover_pct, visibility_m: lw.visibility_m }));
      setWxNote(`ESTIMATED: ${lw.source}, ${lw.observed_at.replace("T", " ")}. Check a pad anemometer and the sky before flying.`);
    } catch (e) { setWxNote(`Live weather unavailable (${(e as Error).message}). Enter the conditions by hand.`); }
  };
  const build = async () => {
    if (!missionId) return;
    setErr(null); setBusy(true);
    try { setCard(await api.post<FlightCard>(`/api/missions/${missionId}/flight-card`, { conditions: cond })); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const toggle = (item: string) => setChecks((o) => {
    const n = { ...o, [item]: !o[item] };
    try { localStorage.setItem(ckKey, JSON.stringify(n)); } catch { /* not remembered */ }
    return n;
  });
  const total = CHECKLIST.reduce((a, [, xs]) => a + xs.length, 0);
  const ticked = CHECKLIST.reduce((a, [, xs]) => a + xs.filter((x) => checks[x]).length, 0);

  return (
    <div className="page stack launchday">
      <div className="toolbar no-print">
        <SelectField label="Mission" value={missionId ?? ""} onChange={(v) => go("launchday", v)}
                     options={missions.map((m) => [m.id, `${m.name} · ${m.vehicle_id} ${m.revision}`] as [string, string])} />
      </div>
      <ErrorBox error={err} />
      <div className="launchday-grid">
        <div className="stack no-print">
          <Card title="Flyer">
            <div className="form grid-form">
              <TextField label="Name" value={profile.name ?? ""} onChange={(v) => setProfile({ ...profile, name: v })} />
              <SelectField label="Organisation" value={profile.organization ?? ""} onChange={(v) => setProfile({ ...profile, organization: v })}
                           options={[["", "—"], ["NAR", "NAR"], ["TRA", "Tripoli (TRA)"], ["UKRA", "UKRA"], ["CAR", "CAR"], ["other", "other"]]} />
              <TextField label="Member number" value={profile.member_number ?? ""} onChange={(v) => setProfile({ ...profile, member_number: v })} />
              <SelectField label="Certification level" value={profile.cert_level === undefined ? "" : String(profile.cert_level)}
                           onChange={(v) => setProfile({ ...profile, cert_level: v === "" ? undefined : Number(v) })}
                           options={[["", "—"], ["0", "none (model rockets)"], ["1", "Level 1"], ["2", "Level 2"], ["3", "Level 3"]]} />
            </div>
            <button className="primary" onClick={saveProfile}>Save flyer</button> {saved && <span className="ok-line">{saved}</span>}
          </Card>
          <Card title="Conditions at the pad">
            <div className="presets"><button onClick={liveWeather} disabled={!mission}>Live weather at the mission site</button></div>
            <div className="form grid-form">
              <NumberField label="Wind speed" unit="m/s" optional value={cond.wind_speed} onChange={(v) => setCond({ ...cond, wind_speed: v })} />
              <NumberField label="Peak gust" unit="m/s" optional value={cond.gust_speed} onChange={(v) => setCond({ ...cond, gust_speed: v })} />
              <NumberField label="Cloud cover" unit="%" optional value={cond.cloud_cover_pct} onChange={(v) => setCond({ ...cond, cloud_cover_pct: v })} />
              <NumberField label="Visibility" unit="m" optional value={cond.visibility_m} onChange={(v) => setCond({ ...cond, visibility_m: v })} />
            </div>
            <label className="check"><input type="checkbox" checked={cond.complex_rocket} onChange={(e) => setCond({ ...cond, complex_rocket: e.target.checked })} />
              Complex rocket (clustered or multi-stage) for the distance table</label>
            {wxNote && <div className="note">{wxNote}</div>}
            <button className="primary" onClick={build} disabled={busy || !missionId}>{busy ? "Simulating…" : "Check and build flight card"}</button>
          </Card>
          <Card title={<>Pre-flight checklist <span className="muted">{ticked}/{total}</span></>}>
            {CHECKLIST.map(([group, items]) => (
              <div key={group} className="ck-group">
                <h3>{group}</h3>
                <ul className="checklist">
                  {items.map((it) => <li key={it}><label><input type="checkbox" checked={!!checks[it]} onChange={() => toggle(it)} /> {it}</label></li>)}
                </ul>
              </div>
            ))}
            <button onClick={() => { setChecks({}); try { localStorage.removeItem(ckKey); } catch { /* ignore */ } }}>Reset checklist</button>
          </Card>
        </div>
        <div className="stack">
          {!card && <Card><div className="empty">Set the conditions and press <strong>Check and build flight card</strong>.</div></Card>}
          {card && <Safety card={card} />}
          {card && <FlightCardView card={card} />}
        </div>
      </div>
    </div>
  );
}

function Safety({ card }: { card: FlightCard }) {
  const s = card.safety;
  const pill = s.status === "GO" ? "OK" : s.status === "NO-GO" ? "FAILED" : "DEGRADED";
  return (
    <Card title="Safety-code review" className="no-print">
      <div className={`banner ${s.status === "GO" ? "go" : s.status === "NO-GO" ? "nogo" : "warn"}`}>
        <StatusPill status={pill} label={s.status === "CHECK" ? "CHECK — some items not set" : s.status} /> Motor needs {s.required_cert.label}.
      </div>
      <table>
        <thead><tr><th>Check</th><th>Status</th><th>Value</th><th>Requirement</th><th>Source</th></tr></thead>
        <tbody>
          {s.items.map((i) => (
            <tr key={i.id}>
              <td>{i.name}</td>
              <td><StatusPill status={i.status === "PASS" ? "OK" : i.status === "FAIL" ? "FAILED" : i.status === "INFO" ? "UNKNOWN" : "DEGRADED"} label={i.status} /></td>
              <td>{i.value}</td><td>{i.requirement}</td><td className="muted">{i.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="note">{s.note}</div>
    </Card>
  );
}

function FlightCardView({ card }: { card: FlightCard }) {
  const v = card.vehicle, m = card.motor, p = card.prediction;
  const row = (k: string, val: string) => <div className="fc-row"><span>{k}</span><strong>{val}</strong></div>;
  return (
    <Card className="flight-card print-area" title={<>Flight card <span className="muted">· {new Date().toLocaleDateString()}</span></>}
          actions={<button className="no-print" onClick={() => window.print()}>Print</button>}>
      <div className="fc-grid">
        <section>
          <h3>Flyer</h3>
          {row("Name", card.flyer.name ?? "—")}
          {row("Organisation / member", `${card.flyer.organization ?? "—"} ${card.flyer.member_number ?? ""}`)}
          {row("Certification", card.flyer.cert_level !== undefined ? `Level ${card.flyer.cert_level}` : "—")}
        </section>
        <section>
          <h3>Rocket</h3>
          {row("Name", `${v.name} (${v.id} ${v.revision})`)}
          {row("Length / diameter", `${fmt(v.length_m, 2)} m / ${fmt(v.diameter_m * 1000)} mm`)}
          {row("Liftoff mass", `${fmt(v.liftoff_mass_kg, 2)} kg (${fmt(v.liftoff_mass_kg * 2.20462, 1)} lb) · ${v.mass_kind}`)}
          {row("CG / CP (loaded)", `${fmt(v.cg_m, 3)} m / ${fmt(v.cp_m, 3)} m from nose`)}
          {row("Stability margin", `${fmt(v.margin_cal, 2)} cal`)}
        </section>
        <section>
          <h3>Motor</h3>
          {row("Motor", `${m.manufacturer} ${m.designation}`)}
          {row("Class / total impulse", `${m.class} · ${fmt(m.total_impulse_ns)} N·s`)}
          {row("Average thrust / burn", `${fmt(m.average_thrust_n)} N / ${fmt(m.burn_time_s, 2)} s`)}
          {row("Curve data", m.data_quality)}
        </section>
        <section>
          <h3>Predicted flight <KindBadge kind="SIMULATED" /></h3>
          {row("Apogee", `${fmt(p.apogee_agl_m)} m (${fmt(p.apogee_agl_m / FT)} ft) AGL${p.apogee_p95_m ? ` · P95 ${fmt(p.apogee_p95_m)} m` : ""}`)}
          {row("Max speed", `${fmt(p.max_speed_mps)} m/s · Mach ${fmt(p.max_mach, 2)}`)}
          {row("Rail exit speed", `${fmt(p.rail_exit_mps, 1)} m/s`)}
          {row("Time to apogee", `${fmt(p.time_to_apogee_s, 1)} s`)}
          {row("Landing", `${fmt(p.landing_distance_m)} m from pad at ${p.landing_rate_mps ? fmt(p.landing_rate_mps, 1) : "—"} m/s`)}
        </section>
        <section>
          <h3>Recovery</h3>
          {card.recovery.length ? card.recovery.map((r) => row(r.name, `${fmt(r.diameter_m * 100)} cm, Cd ${fmt(r.cd, 2)}, at ${r.deploy}`)) : row("Recovery", "none defined")}
          {card.flutter.map((f) => row(`Fin flutter (${f.fin})`, `${fmt(f.min_ratio, 2)}× margin`))}
        </section>
        <section>
          <h3>Safety</h3>
          {card.safety.items.filter((i) => i.status !== "INFO").map((i) => row(i.name, `${i.status} · ${i.value}`))}
          {card.safety.items.filter((i) => i.status === "INFO").map((i) => row(i.name, i.value))}
        </section>
      </div>
      <div className="fc-sign"><span>RSO</span><span>Date / time</span><span>Pad</span></div>
    </Card>
  );
}
