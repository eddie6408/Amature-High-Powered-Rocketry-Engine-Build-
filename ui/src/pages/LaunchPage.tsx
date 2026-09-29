import { useEffect, useRef, useState } from "react";

import { api, type Mission, type Motor } from "../api";
import type { Shape } from "../components/Profile";
import { fmt } from "../components/scale";
import { Card, ErrorBox, KindBadge, NumberField, SelectField, StatusPill, TextField } from "../components/ui";
import type { Dispersion } from "../dispersion";
import { fmtCoords, locationError, parseCoord } from "../geo";
import { go } from "../router";
import { EarthView } from "./EarthView";

interface WindProfile { altitudes: number[]; speeds: number[]; from_deg: number[] }
interface Weather { wind_speed: number; wind_from_deg: number; gust_sigma: number; temperature_c: number;
  humidity_pct: number; pressure_hpa: number | null; rail_elevation_deg: number; launch_into_wind: boolean; rail_azimuth_deg: number;
  wind_profile?: WindProfile | null }
interface LiveWeather { weather: Partial<Weather>; latitude: number; longitude: number; elevation_m: number | null;
  observed_at: string; timezone: string; source: string; kind: string; note: string; peak_gust: number | null }
interface SiteLoc { lat: string; lon: string; alt: number | null; source: "mission" | "gps" | "manual"; accuracy?: number }
const LIVE_MAX_AGE_MS = 10 * 60 * 1000;                     // refresh live weather at countdown if older than this
interface Frames { t: number[]; east: number[]; north: number[]; up: number[]; vz: number[]; speed: number[]; accel_g: number[];
  mach: number[]; thrust: number[]; ax_e: number[]; ax_n: number[]; ax_u: number[]; phase: number[]; wind_e: number[]; wind_n: number[] }
interface LaunchResult { frames: Frames; phases: string[]; events: Array<[number, string]>; summary: Record<string, number | null>;
  calm_summary: Record<string, number | null>; profile: Shape[]; length_m: number; diameter_m: number; cg_m: number; peak_thrust: number;
  recovery: Array<{ name: string; diameter: number }>;
  site: { rail_length: number; elevation_deg: number; azimuth_deg: number; latitude: number; longitude: number; altitude_msl: number };
  weather: Record<string, number>; motor: Record<string, unknown>; vehicle: string;
  landing: { latitude: number; longitude: number } }
interface PadGeometry { profile: Shape[]; length_m: number; diameter_m: number;
  site: { rail_length: number; latitude: number; longitude: number; altitude_msl: number }; vehicle: string }

const PRESETS: Array<[string, Partial<Weather>]> = [
  ["Standard calm day", { wind_speed: 0, gust_sigma: 0, temperature_c: 15, humidity_pct: 0, pressure_hpa: null }],
  ["Light breeze", { wind_speed: 3, gust_sigma: 0.5, temperature_c: 18, humidity_pct: 40 }],
  ["Hot humid afternoon", { wind_speed: 5, gust_sigma: 1.5, temperature_c: 36, humidity_pct: 80, pressure_hpa: 1005 }],
  ["Cold windy morning", { wind_speed: 8, gust_sigma: 2.5, temperature_c: -5, humidity_pct: 30, pressure_hpa: 1028 }],
];
const CHECKLIST = ["Rocket on the rail", "Recovery / altimeters armed", "Igniter installed", "Range and sky clear", "RSO approval"];
type Stage = "setup" | "armed" | "countdown" | "hold" | "flight" | "complete" | "failed";

export function LaunchPage({ missionId, motorKey }: { missionId?: string; motorKey?: string }) {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [motors, setMotors] = useState<Motor[]>([]);
  const [motor, setMotor] = useState(motorKey ?? "");
  const [w, setW] = useState<Weather>({ wind_speed: 3, wind_from_deg: 270, gust_sigma: 0.5, temperature_c: 18, humidity_pct: 40,
    pressure_hpa: null, rail_elevation_deg: 87, launch_into_wind: true, rail_azimuth_deg: 0 });
  const [checks, setChecks] = useState<boolean[]>(CHECKLIST.map(() => false));
  const [countLen, setCountLen] = useState(10);
  const [stage, setStage] = useState<Stage>("setup");
  const [tMinus, setTMinus] = useState(10);
  const [res, setRes] = useState<LaunchResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [t, setT] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [paused, setPaused] = useState(false);
  const pending = useRef<Promise<LaunchResult> | null>(null);
  const [pad, setPad] = useState<PadGeometry | null>(null);
  const [disp, setDisp] = useState<Dispersion | null>(null);
  const [loc, setLoc] = useState<SiteLoc>({ lat: "", lon: "", alt: null, source: "mission" });
  const [live, setLive] = useState<(LiveWeather & { fetchedAt: number; edited: boolean }) | null>(null);
  const [locMsg, setLocMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<"" | "locating" | "weather">("");
  const wRef = useRef(w);
  wRef.current = w;

  useEffect(() => {
    api.get<Mission[]>("/api/missions").then((m) => { setMissions(m); if (!missionId && m.length) go("launch", m[0].id, ...(motorKey ? [motorKey] : [])); });
    api.get<Motor[]>("/api/motors").then(setMotors);
  }, []);
  const mission = missions.find((m) => m.id === missionId);
  useEffect(() => {
    setPad(null);
    if (missionId) api.get<PadGeometry>(`/api/missions/${missionId}/pad`).then(setPad).catch(() => undefined);
    setDisp(null);
    if (missionId) api.get<Dispersion>(`/api/missions/${missionId}/dispersion`).then(setDisp).catch(() => undefined);
  }, [missionId]);
  useEffect(() => { if (mission && !motorKey && !motor) setMotor(mission.motor_key); }, [mission]);
  const missionSite = (): SiteLoc => ({ lat: pad ? String(pad.site.latitude) : "", lon: pad ? String(pad.site.longitude) : "",
    alt: pad?.site.altitude_msl ?? null, source: "mission" });
  useEffect(() => { if (pad) setLoc((o) => (o.source === "mission" ? missionSite() : o)); }, [pad]);

  const WIND_KEYS: Array<keyof Weather> = ["wind_speed", "wind_from_deg", "gust_sigma"];
  const set = (k: keyof Weather, v: number | boolean | null) => {
    // a hand-entered wind replaces the live wind-aloft profile
    setW((o) => ({ ...o, [k]: v, ...(WIND_KEYS.includes(k) ? { wind_profile: null } : {}) }));
    if (!["rail_elevation_deg", "launch_into_wind", "rail_azimuth_deg"].includes(k)) setLive((o) => (o ? { ...o, edited: true } : o));
  };
  const lat = parseCoord(loc.lat, "lat");
  const lon = parseCoord(loc.lon, "lon");
  const locOk = lat !== null && lon !== null && loc.alt !== null;
  const armed = checks.every(Boolean) && locOk;

  const fetchWeather = (la: number, lo: number, gpsAlt?: number | null): Promise<Weather | null> => {
    setBusy("weather");
    return api.get<LiveWeather>(`/api/weather/live?lat=${la}&lon=${lo}`)
      .then((lw) => {
        const nw: Weather = { ...wRef.current, ...lw.weather, wind_profile: lw.weather.wind_profile ?? null };
        setW(nw);
        setLive({ ...lw, fetchedAt: Date.now(), edited: false });
        // model terrain height when the device gives no usable altitude (most laptops and many phones)
        if ((gpsAlt === null || gpsAlt === undefined) && lw.elevation_m !== null) setLoc((o) => ({ ...o, alt: Math.round(lw.elevation_m!) }));
        setLocMsg(null);
        return nw;
      })
      .catch((e) => {
        setLocMsg(`Location is set, but live weather is unavailable: ${(e as Error).message}. `
          + "Enter the weather by hand below, and the pad altitude if it is blank.");
        return null;
      })
      .finally(() => setBusy(""));
  };
  const myLocation = () => {
    setLocMsg(null);
    if (!("geolocation" in navigator)) { setLocMsg(locationError("unsupported")); return; }
    if (!window.isSecureContext) { setLocMsg(locationError("insecure")); return; }
    setBusy("locating");
    navigator.geolocation.getCurrentPosition((pos) => {
      const c = pos.coords;
      const gpsAlt = c.altitude !== null && (c.altitudeAccuracy ?? 99) <= 15 ? Math.round(c.altitude) : null;
      // no usable device altitude: blank until the weather service supplies terrain height (or you type it)
      setLoc({ lat: c.latitude.toFixed(6), lon: c.longitude.toFixed(6), alt: gpsAlt, source: "gps", accuracy: c.accuracy });
      fetchWeather(c.latitude, c.longitude, gpsAlt);
    }, (e) => { setBusy(""); setLocMsg(locationError(e.code)); }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 60000 });
  };
  const weatherHere = () => {
    if (lat === null || lon === null) { setLocMsg("Enter a valid latitude and longitude first."); return; }
    fetchWeather(lat, lon, loc.source === "gps" ? loc.alt : null);
  };
  const editLoc = (patch: Partial<SiteLoc>) => setLoc((o) => ({ ...o, ...patch, source: "manual", accuracy: undefined }));

  // countdown
  useEffect(() => {
    if (stage !== "countdown") return;
    if (tMinus <= 0) {
      pending.current!.then((r) => { setRes(r); setT(0); setStage("flight"); })
        .catch((e) => { setErr((e as Error).message); setStage("failed"); });
      return;
    }
    const id = setTimeout(() => setTMinus((x) => x - 1), 1000);
    return () => clearTimeout(id);
  }, [stage, tMinus]);

  // flight playback
  useEffect(() => {
    if (stage !== "flight" || paused || !res) return;
    let raf = 0;
    let last = performance.now();
    const tEnd = res.frames.t[res.frames.t.length - 1];
    const loop = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      setT((x) => {
        const nx = Math.min(tEnd, x + dt * speed);
        if (nx >= tEnd) setStage("complete");
        return nx;
      });
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [stage, paused, speed, res]);

  const startCountdown = () => {
    setErr(null);
    const location = locOk ? { latitude: lat, longitude: lon, altitude_msl: loc.alt } : null;
    // live weather is re-read at countdown when it is stale and hasn't been edited by hand
    const stale = live && !live.edited && Date.now() - live.fetchedAt > LIVE_MAX_AGE_MS && lat !== null && lon !== null;
    const wx: Promise<Weather> = stale ? fetchWeather(lat!, lon!, loc.source === "gps" ? loc.alt : null).then((x) => x ?? wRef.current)
      : Promise.resolve(w);
    pending.current = wx.then((weather) => api.post<LaunchResult>(`/api/missions/${missionId}/launch`,
      { weather, motor_key: motor || null, location }));
    pending.current.catch(() => undefined);
    setTMinus(countLen);
    setStage("countdown");
  };
  const recycle = () => { setStage("setup"); setChecks(CHECKLIST.map(() => false)); setRes(null); setT(0); setPaused(false); };

  const selMotor = motors.find((m) => m.key === motor);
  return (
    <div className="page stack">
      <div className="toolbar">
        <h1>Launch simulator</h1>
        <KindBadge kind="SIMULATED" />
        <SelectField label="Mission (vehicle + site)" value={missionId ?? ""} onChange={(v) => { go("launch", v); recycle(); }}
                     options={missions.map((m) => [m.id, `${m.name} · ${m.vehicle_id} ${m.revision}`] as [string, string])} />
        <SelectField label="Motor" value={motor} onChange={(v) => { setMotor(v); recycle(); }}
                     options={motors.map((m) => [m.key, `${m.manufacturer} ${m.designation} (${m.data_quality.toLowerCase()})`] as [string, string])} />
        {selMotor && !["CERTIFIED", "MANUFACTURER", "MEASURED"].includes(selMotor.data_quality) &&
          <span className="warn-line">motor data is {selMotor.data_quality} - illustration only</span>}
      </div>
      <ErrorBox error={err} />
      <div className="launch-grid">
        <div className="stack">
          <SceneCard res={res} pad={pad} t={t} stage={stage} tMinus={tMinus} weather={w} location={locOk ? { latitude: lat!, longitude: lon!, altitude_msl: loc.alt! } : null} dispersion={disp} />
          {res && (stage === "flight" || stage === "complete") && <Playback res={res} t={t} setT={setT} speed={speed} setSpeed={setSpeed}
            paused={paused} setPaused={setPaused} stage={stage} setStage={setStage} />}
          {res && (stage === "flight" || stage === "complete") && <Readouts res={res} t={t} />}
          {res && stage === "complete" && <Results res={res} />}
        </div>
        <div className="stack">
          <Card title="Launch sequence">
            {stage === "setup" && (
              <>
                <ul className="checklist">
                  {CHECKLIST.map((c, i) => (
                    <li key={c}><label><input type="checkbox" checked={checks[i]} onChange={(e) => setChecks((o) => o.map((x, k) => (k === i ? e.target.checked : x)))} /> {c}</label></li>
                  ))}
                </ul>
                <NumberField label="Countdown" unit="s" step="1" value={countLen} onChange={(v) => setCountLen(Math.max(3, Math.min(60, v ?? 10)))} />
                <button className="primary" disabled={!armed || !missionId} onClick={() => setStage("armed")}>ARM</button>
                {!checks.every(Boolean) && <div className="note">Complete the pad checklist to arm.</div>}
                {!locOk && <div className="note">Set the launch location (latitude, longitude, pad altitude) to arm.</div>}
              </>
            )}
            {stage === "armed" && (
              <>
                <div className="banner warn"><StatusPill status="WARN" label="ARMED" /> ready for countdown</div>
                <button className="primary" onClick={startCountdown}>START COUNTDOWN</button>
                <button onClick={() => setStage("setup")}>Disarm</button>
              </>
            )}
            {(stage === "countdown" || stage === "hold") && (
              <>
                <div className="countdown" aria-live="assertive">T-{tMinus}</div>
                {stage === "countdown" ? <button onClick={() => setStage("hold")}>HOLD</button>
                  : <button className="primary" onClick={() => setStage("countdown")}>RESUME</button>}
                <button className="danger" onClick={() => { setStage("armed"); pending.current = null; }}>ABORT</button>
              </>
            )}
            {(stage === "flight" || stage === "complete" || stage === "failed") && (
              <>
                <div className="countdown">{stage === "failed" ? "NO FLIGHT" : `T+${fmt(t, 1)}`}</div>
                <button onClick={recycle}>Recycle to pad</button>
              </>
            )}
          </Card>
          <Card title="Launch location">
            <div className="presets">
              <button className="primary" onClick={myLocation} disabled={busy !== "" || stage !== "setup"}
                      title="Use this device's location services, then load the current weather there">
                <span aria-hidden="true">◎ </span>{busy === "locating" ? "Locating…" : "My location"}</button>
              <button onClick={weatherHere} disabled={busy !== "" || stage !== "setup" || lat === null || lon === null}>
                {busy === "weather" ? "Loading weather…" : "Live weather here"}</button>
              <button className="link" onClick={() => { setLoc(missionSite()); setLocMsg(null); }} disabled={stage !== "setup"}>
                Use mission site</button>
            </div>
            <fieldset disabled={stage !== "setup"} className="form grid-form">
              <TextField label="Latitude" value={loc.lat} onChange={(v) => editLoc({ lat: v })} />
              <TextField label="Longitude" value={loc.lon} onChange={(v) => editLoc({ lon: v })} />
              <NumberField label="Pad altitude (above sea level)" unit="m" value={loc.alt} optional
                           onChange={(v) => editLoc({ alt: v })} />
            </fieldset>
            <div className="note">
              {lat !== null && lon !== null ? fmtCoords(lat, lon) : <span className="warn-line">
                {loc.lat || loc.lon ? "Can't read these coordinates. Use decimal degrees (40.1234, -105.2) or 40°7'24\"N." : "No coordinates yet."}</span>}
              {" · "}{loc.source === "gps" ? `device location${loc.accuracy ? ` ±${fmt(loc.accuracy)} m` : ""}`
                : loc.source === "manual" ? "entered by hand" : "mission site"}
            </div>
            {live && <div className="note">
              <KindBadge kind={live.kind} /> {live.edited ? "Live weather, edited by hand" : "Live weather"} from {live.source},
              {" "}{live.observed_at?.replace("T", " ")} ({live.timezone}){w.wind_profile ? `, wind aloft to ${Math.max(...w.wind_profile.altitudes)} m` : ""}.
              {!live.edited && " Refreshed at countdown if older than 10 min."} {live.note}
            </div>}
            {locMsg && <div className="banner warn" role="status">{locMsg}</div>}
          </Card>
          <Card title="Weather">
            <div className="presets">
              {PRESETS.map(([n, p]) => <button key={n} disabled={stage !== "setup" && stage !== "complete" && stage !== "failed"}
                onClick={() => { setW((o) => ({ ...o, ...p, wind_profile: null })); setLive(null);
                  if (stage === "complete" || stage === "failed") recycle(); }}>{n}</button>)}
            </div>
            <fieldset disabled={stage !== "setup"} className="form grid-form">
              <NumberField label="Wind speed (10 m)" unit="m/s" value={w.wind_speed} onChange={(v) => set("wind_speed", v ?? 0)} />
              <NumberField label="Wind from" unit="°" value={w.wind_from_deg} onChange={(v) => set("wind_from_deg", v ?? 0)} />
              <NumberField label="Gusts (1σ)" unit="m/s" value={w.gust_sigma} onChange={(v) => set("gust_sigma", v ?? 0)} />
              <NumberField label="Temperature" unit="°C" value={w.temperature_c} onChange={(v) => set("temperature_c", v ?? 15)} />
              <NumberField label="Humidity" unit="%" value={w.humidity_pct} onChange={(v) => set("humidity_pct", Math.max(0, Math.min(100, v ?? 0)))} />
              <NumberField label="Station pressure (blank = standard)" unit="hPa" optional value={w.pressure_hpa} onChange={(v) => set("pressure_hpa", v)} />
              <NumberField label="Rail angle above horizontal" unit="°" value={w.rail_elevation_deg} onChange={(v) => set("rail_elevation_deg", v ?? 87)} />
              <SelectField label="Rail pointing" value={w.launch_into_wind ? "wind" : "fixed"} onChange={(v) => set("launch_into_wind", v === "wind")}
                           options={[["wind", "into the wind"], ["fixed", "fixed azimuth"]]} />
              {!w.launch_into_wind && <NumberField label="Rail azimuth" unit="°" value={w.rail_azimuth_deg} onChange={(v) => set("rail_azimuth_deg", v ?? 0)} />}
            </fieldset>
            {res && <div className="note">Air density ×{fmt(res.weather.density_ratio, 3)} of standard at the site ·
              density altitude {fmt(res.weather.density_altitude_m)} m · {fmt(res.weather.pressure_hpa, 1)} hPa</div>}
          </Card>
          {res && <MiniMap res={res} t={t} />}
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------- helpers */
function frameAt(f: Frames, t: number) {
  const n = f.t.length;
  const dt = f.t[1] - f.t[0];
  const i = Math.max(0, Math.min(n - 2, Math.floor(t / dt)));
  const a = Math.max(0, Math.min(1, (t - f.t[i]) / dt));
  const L = (k: keyof Frames) => (f[k] as number[])[i] * (1 - a) + (f[k] as number[])[i + 1] * a;
  return { i, east: L("east"), north: L("north"), up: L("up"), vz: L("vz"), speed: L("speed"), g: L("accel_g"), mach: L("mach"),
           thrust: L("thrust"), ax_e: L("ax_e"), ax_u: L("ax_u"), ax_n: L("ax_n"), phase: f.phase[i], wind_e: L("wind_e"), wind_n: L("wind_n") };
}

const EVENT_LABEL: Record<string, string> = { liftoff: "LIFTOFF", rail_exit: "RAIL EXIT", burnout: "BURNOUT", apogee: "APOGEE",
  landing: "TOUCHDOWN", ground_impact: "IMPACT" };
const evLabel = (e: string) => EVENT_LABEL[e] ?? (e.startsWith("deploy:") ? `${e.slice(7).toUpperCase()} DEPLOYED` : e.toUpperCase());

/* ------------------------------------------------------------------ scene card */
type View = "2d" | "earth";
function savedView(): View {
  try { return localStorage.getItem("aerodyne-launch-view") === "earth" ? "earth" : "2d"; } catch { return "2d"; }
}

function SceneCard(props: { res: LaunchResult | null; pad: PadGeometry | null; t: number; stage: Stage; tMinus: number; weather: Weather;
  location: { latitude: number; longitude: number; altitude_msl: number } | null; dispersion: Dispersion | null }) {
  const [view, setView] = useState<View>(savedView);
  const pick = (v: View) => { setView(v); try { localStorage.setItem("aerodyne-launch-view", v); } catch { /* not remembered */ } };
  return (
    <div className="scene card">
      <div className="scene-tabs" role="tablist" aria-label="Scene view">
        <button role="tab" aria-selected={view === "2d"} className={view === "2d" ? "on" : ""} onClick={() => pick("2d")}>Illustrated</button>
        <button role="tab" aria-selected={view === "earth"} className={view === "earth" ? "on" : ""} onClick={() => pick("earth")}>Earth 3D</button>
      </div>
      {view === "2d" ? <Scene res={props.res} pad={props.pad} t={props.t} stage={props.stage} tMinus={props.tMinus} weather={props.weather} />
        : <EarthView {...props} />}
    </div>
  );
}

/* ---------------------------------------------------------------------- scene */
const CLOUDS: Array<[number, number, number]> = [[0.12, 0.2, 1.1], [0.46, 0.12, 0.8], [0.78, 0.26, 1.3], [0.32, 0.36, 0.7], [0.93, 0.08, 0.9]];

function Scene({ res, pad, t, stage, tMinus, weather }: { res: LaunchResult | null; pad: PadGeometry | null; t: number; stage: Stage; tMinus: number; weather: Weather }) {
  const W = 1200, H = 640;
  const zoom = useRef(8);
  const onPad = !res || stage === "setup" || stage === "armed" || stage === "countdown" || stage === "hold" || stage === "failed";
  const fr = res && !onPad ? frameAt(res.frames, t) : null;
  const geo = res ?? pad;
  const len = geo?.length_m ?? 1.25;
  const dia = geo?.diameter_m ?? 0.066;
  // camera: height of view in metres, smoothed
  const target = fr ? Math.max(len * 4, fr.up * 1.7 + len * 6) : len * 4;
  zoom.current += (target - zoom.current) * (onPad ? 1 : 0.08);
  const Hv = zoom.current;
  const k = H / Hv;                                         // px per metre
  const east = fr?.east ?? 0;
  const up = fr?.up ?? 0;
  const bottom = Math.max(-0.14 * Hv, up - 0.55 * Hv);
  const X = (e: number) => W / 2 + (e - east) * k;
  const Y = (u: number) => H - (u - bottom) * k;
  // rocket: true scale when close, never shorter than 90 px nor thinner than 11 px (fins stay readable)
  const rs = Math.max(k, 90 / len);
  const fat = Math.max(1, 11 / (dia * rs));
  const elev = ((res?.site.elevation_deg ?? weather.rail_elevation_deg) * Math.PI) / 180;
  const az = ((res?.site.azimuth_deg ?? (weather.launch_into_wind ? weather.wind_from_deg : weather.rail_azimuth_deg)) * Math.PI) / 180;
  const railE = Math.cos(elev) * Math.sin(az);
  let dx = fr ? fr.ax_e : railE, dy = fr ? fr.ax_u : Math.sin(elev);
  const phaseName = fr && res ? res.phases[fr.phase] : "pad";
  const deployed = res && fr ? res.events.filter(([te, e]) => e.startsWith("deploy:") && te <= t).map(([, e]) => e.slice(7)) : [];
  if (deployed.length) { dx = 0.3; dy = 1; }                // hangs under canopy
  const n = Math.hypot(dx, dy) || 1; dx /= n; dy /= n;
  const baseX = X(east), baseY = Y(up);                     // aft end of the rocket
  const toScreen = (xb: number, yb: number): [number, number] => {
    const along = len - xb;                                 // distance forward of the aft end
    const yy = yb * fat;
    return [baseX + (along * dx - yy * dy) * rs, baseY - (along * dy + yy * dx) * rs];
  };
  const shapes = (geo?.profile ?? []).filter((s) => s.kind === "body" || s.kind === "fin");
  const noseIdx = shapes.findIndex((s) => s.kind === "body");
  const radPx = (dia / 2) * fat * rs;
  const [ax0, ay0] = toScreen(len, 0);
  const [nx0, ny0] = toScreen(0, 0);
  const thrustFrac = fr && fr.thrust > 0 ? fr.thrust / (res!.peak_thrust || 1) : 0;
  const flicker = 0.9 + 0.2 * Math.abs(Math.sin(t * 53));
  const flameLen = thrustFrac > 0 ? (0.35 + 0.9 * thrustFrac) * len * rs * flicker : 0;
  // body-normal for the shading gradient
  const gx1 = ax0 - dy * radPx, gy1 = ay0 - dx * radPx, gx2 = ax0 + dy * radPx, gy2 = ay0 + dx * radPx;

  // smoke: puffs laid along the powered/coast track, growing and drifting downwind with age
  const puffs: Array<[number, number, number, number]> = [];
  if (res && fr) {
    const f = res.frames;
    const tEnd = Math.min(t, res.events.find(([, e]) => e === "apogee")?.[0] ?? t);
    const burnout = res.events.find(([, e]) => e === "burnout")?.[0] ?? tEnd;
    for (let tt = 0; tt <= tEnd; tt += 0.12) {
      const g = frameAt(f, tt);
      const age = t - tt;
      const dense = tt <= burnout ? 1 : Math.max(0, 1 - (tt - burnout) / 2.5);   // thins out after burnout
      if (dense <= 0.02) continue;
      const r = (dia * 2.2 + age * 0.9) * k + 3 + Math.min(12, age * 3);
      puffs.push([X(g.east + g.wind_e * age * 0.6), Y(g.up + 0.2 * age), r, Math.max(0, 0.55 * dense * (1 - age / 40))]);
    }
  }
  const landingX = res && fr && deployed.length ? X(res.frames.east[res.frames.east.length - 1]) : null;
  const recent = res && fr ? res.events.filter(([te]) => te <= t && t - te < 2.5) : [];
  const ticks = niceAlt(bottom, bottom + Hv);
  const windAt = fr ? Math.hypot(fr.wind_e, fr.wind_n) : weather.wind_speed;
  const windTo = ((weather.wind_from_deg + 180) * Math.PI) / 180;
  const windDriftPx = (weather.wind_speed * t * 4) % (W * 1.4);
  const skyDark = Math.min(0.55, up / 9000);                // deeper blue with altitude
  const railLen = Math.max(geo?.site.rail_length ?? 2, 1) * rs;
  const padX = X(0), padY = Y(0);
  const hillY = Y(0) - 26 - Math.min(120, Hv * 0.02 * k);
  const railTop: [number, number] = [padX + railE * railLen, padY - 10 - Math.sin(elev) * railLen];
  const clock = fr ? `T+${fmtClock(t)}` : stage === "countdown" || stage === "hold" ? `T-${fmtClock(tMinus)}` : "T-00:00.0";
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" shapeRendering="geometricPrecision"
         aria-label={`Launch scene, ${phaseName}, altitude ${fmt(up)} m`}>
      <defs>
        <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#1f5fae" /><stop offset="0.55" stopColor="#5d9bdc" /><stop offset="1" stopColor="#d6e9f7" />
        </linearGradient>
        <linearGradient id="ground" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#6e8e45" /><stop offset="1" stopColor="#3f5a28" />
        </linearGradient>
        <linearGradient id="hills" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#7d93a8" /><stop offset="1" stopColor="#9fb3c4" />
        </linearGradient>
        <linearGradient id="body" gradientUnits="userSpaceOnUse" x1={gx1} y1={gy1} x2={gx2} y2={gy2}>
          <stop offset="0" stopColor="#8e9aa4" /><stop offset="0.35" stopColor="#ffffff" /><stop offset="0.7" stopColor="#dfe5ea" /><stop offset="1" stopColor="#7c8893" />
        </linearGradient>
        <linearGradient id="nose" gradientUnits="userSpaceOnUse" x1={gx1} y1={gy1} x2={gx2} y2={gy2}>
          <stop offset="0" stopColor="#0b6f60" /><stop offset="0.4" stopColor="#35e0c2" /><stop offset="1" stopColor="#08584c" />
        </linearGradient>
        <linearGradient id="fin" gradientUnits="userSpaceOnUse" x1={gx1} y1={gy1} x2={gx2} y2={gy2}>
          <stop offset="0" stopColor="#0f1a22" /><stop offset="0.5" stopColor="#2b3a46" /><stop offset="1" stopColor="#0f1a22" />
        </linearGradient>
        <radialGradient id="glow"><stop offset="0" stopColor="#fff3c4" stopOpacity="0.9" /><stop offset="1" stopColor="#ff8a2a" stopOpacity="0" /></radialGradient>
        <linearGradient id="flameOuter" gradientUnits="userSpaceOnUse" x1={ax0} y1={ay0} x2={ax0 - dx * flameLen} y2={ay0 + dy * flameLen}>
          <stop offset="0" stopColor="#ffd35a" /><stop offset="0.5" stopColor="#ff7a1f" stopOpacity="0.85" /><stop offset="1" stopColor="#ff3d00" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="flameCore" gradientUnits="userSpaceOnUse" x1={ax0} y1={ay0} x2={ax0 - dx * flameLen * 0.55} y2={ay0 + dy * flameLen * 0.55}>
          <stop offset="0" stopColor="#ffffff" /><stop offset="1" stopColor="#fff1a8" stopOpacity="0" />
        </linearGradient>
        <filter id="soft" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.2" /></filter>
      </defs>
      <rect x="0" y="0" width={W} height={H} fill="url(#sky)" />
      <rect x="0" y="0" width={W} height={H} fill="#061a3a" opacity={skyDark} />
      {/* clouds drift with the wind */}
      {CLOUDS.map(([cx, cy, sc], i) => {
        const span = W * 1.4;
        const x = (((cx * span + (Math.sin(windTo) >= 0 ? 1 : -1) * windDriftPx) % span) + span) % span - W * 0.2;
        const y = cy * H + (up * k * 0.08) % H;
        return (
          <g key={i} opacity={0.85} filter="url(#soft)">
            <ellipse cx={x} cy={y} rx={70 * sc} ry={18 * sc} fill="#ffffff" />
            <ellipse cx={x + 30 * sc} cy={y - 12 * sc} rx={40 * sc} ry={16 * sc} fill="#ffffff" />
            <ellipse cx={x - 34 * sc} cy={y - 6 * sc} rx={34 * sc} ry={13 * sc} fill="#f4f8fb" />
          </g>
        );
      })}
      {ticks.map((a) => (
        <g key={a} className="alt-tick"><line x1={0} x2={W} y1={Y(a)} y2={Y(a)} /><text x={W - 12} y={Y(a) - 5} textAnchor="end">{fmt(a)} m</text></g>
      ))}
      {/* distant hills and the field */}
      {hillY < H && <path d={`M0,${hillY + 18} C${W * 0.15},${hillY - 14} ${W * 0.3},${hillY + 10} ${W * 0.45},${hillY} S${W * 0.75},${hillY - 22} ${W},${hillY + 6} L${W},${padY} L0,${padY} Z`} fill="url(#hills)" opacity={0.9} />}
      <rect x="0" y={padY} width={W} height={Math.max(0, H - padY)} fill="url(#ground)" />
      {padY < H && Array.from({ length: 24 }, (_, i) => (
        <line key={i} className="furrow" x1={((i * 97 - east * k) % W + W) % W} y1={padY + 4 + (i % 5) * 7} x2={((i * 97 - east * k) % W + W) % W + 18} y2={padY + 4 + (i % 5) * 7} />
      ))}
      {/* pad: blast plate, tripod stand and rail */}
      {padY < H + 20 && (
        <g className="pad-hw">
          <line x1={padX} y1={padY - 10} x2={padX - 22} y2={padY} /><line x1={padX} y1={padY - 10} x2={padX + 22} y2={padY} />
          <line x1={padX} y1={padY - 10} x2={padX + 4} y2={padY + 2} />
          <rect x={padX - 16} y={padY - 13} width={32} height={4} rx={1} className="plate" />
          <line className="rail" x1={padX} y1={padY - 10} x2={railTop[0]} y2={railTop[1]} />
          <circle cx={padX + 70} cy={padY - 3} r={3} className="flag" />
          <line x1={padX + 70} y1={padY} x2={padX + 70} y2={padY - 22} className="pole" />
          <path d={`M${padX + 70},${padY - 22} l14,4 l-14,4 Z`} className="flag" />
        </g>
      )}
      {landingX !== null && <g className="landing-mark"><line x1={landingX - 7} y1={padY - 7} x2={landingX + 7} y2={padY + 7} /><line x1={landingX - 7} y1={padY + 7} x2={landingX + 7} y2={padY - 7} /></g>}
      {/* smoke */}
      {puffs.map(([px, py, r, o], i) => <circle key={i} cx={px} cy={py} r={r} fill="#f2f2f0" opacity={o} />)}
      {/* parachutes */}
      {deployed.map((name, i) => {
        const dev = res!.recovery.find((r) => r.name === name);
        const r = Math.max(16, (dev?.diameter ?? 0.5) * 0.5 * rs);
        const top = ny0 - (46 + i * 30) - r * 0.4;
        const gores = 6;
        return (
          <g key={name} className="chute">
            {Array.from({ length: gores }, (_, g) => {
              const a0 = Math.PI + (g / gores) * Math.PI, a1 = Math.PI + ((g + 1) / gores) * Math.PI;
              return <path key={g} d={`M${nx0},${top} L${nx0 + r * Math.cos(a0)},${top + r * 0.7 * Math.sin(a0)} A${r},${r * 0.7} 0 0 1 ${nx0 + r * Math.cos(a1)},${top + r * 0.7 * Math.sin(a1)} Z`}
                           fill={g % 2 ? (i === 0 ? "#ff5a4f" : "#ffb020") : "#ffffff"} />;
            })}
            <line x1={nx0 - r} y1={top} x2={nx0} y2={ny0} /><line x1={nx0 + r} y1={top} x2={nx0} y2={ny0} />
            <line x1={nx0 - r * 0.4} y1={top} x2={nx0} y2={ny0} /><line x1={nx0 + r * 0.4} y1={top} x2={nx0} y2={ny0} />
          </g>
        );
      })}
      {/* flame */}
      {flameLen > 0 && (
        <g>
          <circle cx={ax0 - dx * flameLen * 0.2} cy={ay0 + dy * flameLen * 0.2} r={radPx * 3.2 + flameLen * 0.18} fill="url(#glow)" />
          <polygon fill="url(#flameOuter)" points={`${ax0 - dy * radPx * 0.85},${ay0 - dx * radPx * 0.85} ${ax0 + dy * radPx * 0.85},${ay0 + dx * radPx * 0.85} ${ax0 - dx * flameLen},${ay0 + dy * flameLen}`} />
          <polygon fill="url(#flameCore)" points={`${ax0 - dy * radPx * 0.45},${ay0 - dx * radPx * 0.45} ${ax0 + dy * radPx * 0.45},${ay0 + dx * radPx * 0.45} ${ax0 - dx * flameLen * 0.55},${ay0 + dy * flameLen * 0.55}`} />
        </g>
      )}
      {/* rocket, from the design profile */}
      <g className="rocket">
        {shapes.map((s, i) => (
          <polygon key={i} fill={s.kind === "fin" ? "url(#fin)" : i === noseIdx ? "url(#nose)" : "url(#body)"}
                   points={s.points.map(([xb, yb]) => toScreen(xb, yb).map((v) => v.toFixed(1)).join(",")).join(" ")} />
        ))}
        <polygon className="nozzle" points={[toScreen(len, dia * 0.28), toScreen(len + dia * 0.35, dia * 0.34), toScreen(len + dia * 0.35, -dia * 0.34), toScreen(len, -dia * 0.28)].map((p) => p.map((v) => v.toFixed(1)).join(",")).join(" ")} />
      </g>
      {/* wind indicator */}
      <g transform={`translate(${W - 170},96) scale(1.3)`} className="windsock">
        <circle r="38" />
        <line x1={-Math.sin(windTo) * 26} y1={Math.cos(windTo) * 26} x2={Math.sin(windTo) * 26} y2={-Math.cos(windTo) * 26} />
        <path d="M0,-9 L6,6 L-6,6 Z" transform={`translate(${Math.sin(windTo) * 26},${-Math.cos(windTo) * 26}) rotate(${(windTo * 180) / Math.PI})`} className="arrow" />
        <text y="-44" textAnchor="middle">N</text>
        <text y="58" textAnchor="middle">{fmt(windAt, 1)} m/s</text>
      </g>
      {recent.map(([te, e], i) => (
        <text key={e} className="event-flash" x={W / 2 + 80} y={140 + i * 30} opacity={Math.max(0, 1 - (t - te) / 2.5)}>{evLabel(e)}</text>
      ))}
      {(stage === "countdown" || stage === "hold") && (
        <g>
          <rect x={W / 2 - 170} y={H / 2 - 78} width={340} height={116} rx={16} className="count-panel" />
          <text className="big-count" x={W / 2} y={H / 2 + 8} textAnchor="middle">T-{tMinus}</text>
          {stage === "hold" && <text className="hold" x={W / 2} y={H / 2 + 30} textAnchor="middle">HOLD</text>}
        </g>
      )}
      {stage === "setup" && !res && <text className="scene-note top" x={W / 2} y={44} textAnchor="middle">Complete the checklist, arm, and start the countdown</text>}
      {/* broadcast-style overlay */}
      <g className="hud" transform={`translate(18,${H - 86}) scale(1.5)`}>
        <rect width={430} height={46} rx={10} />
        <text x={16} y={29} className="hud-big">{clock}</text>
        <text x={170} y={19}>ALTITUDE</text><text x={170} y={37} className="hud-val">{fmt(up)} m</text>
        <text x={262} y={19}>SPEED</text><text x={262} y={37} className="hud-val">{fmt(fr?.speed ?? 0)} m/s</text>
        <text x={352} y={19}>{phaseName.toUpperCase()}</text><text x={352} y={37} className="hud-val">{fmt(fr?.mach ?? 0, 2)} M</text>
      </g>
      <text className="scene-note" x={W - 14} y={H - 12} textAnchor="end">SIMULATED · rocket enlarged when zoomed out</text>
    </svg>
  );
}

function fmtClock(s: number): string {
  const m = Math.floor(Math.abs(s) / 60);
  const sec = Math.abs(s) - m * 60;
  return `${String(m).padStart(2, "0")}:${sec.toFixed(1).padStart(4, "0")}`;
}

function niceAlt(lo: number, hi: number): number[] {
  const span = hi - lo;
  const step = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000].find((s) => span / s <= 6) ?? 10000;
  const out: number[] = [];
  for (let a = Math.ceil(Math.max(0, lo) / step) * step; a <= hi; a += step) out.push(a);
  return out;
}

/* -------------------------------------------------------------------- controls */
function Playback({ res, t, setT, speed, setSpeed, paused, setPaused, stage, setStage }: {
  res: LaunchResult; t: number; setT: (v: number) => void; speed: number; setSpeed: (v: number) => void;
  paused: boolean; setPaused: (v: boolean) => void; stage: Stage; setStage: (s: Stage) => void }) {
  const tEnd = res.frames.t[res.frames.t.length - 1];
  return (
    <div className="playback">
      <button onClick={() => { if (stage === "complete") { setT(0); setStage("flight"); setPaused(false); } else setPaused(!paused); }}>
        {stage === "complete" ? "Replay" : paused ? "Play" : "Pause"}</button>
      {[1, 2, 5, 10, 25].map((s) => <button key={s} className={speed === s ? "primary" : ""} onClick={() => setSpeed(s)}>{s}×</button>)}
      <input type="range" min={0} max={tEnd} step={0.05} value={t} aria-label="Flight time"
             onChange={(e) => { setT(Number(e.target.value)); if (stage === "complete") setStage("flight"); }} />
      {res.events.filter(([, e]) => e !== "liftoff").map(([te, e]) => (
        <button key={e} className="link" onClick={() => { setT(te); if (stage === "complete") setStage("flight"); }}>{evLabel(e).toLowerCase()}</button>
      ))}
    </div>
  );
}

function Readouts({ res, t }: { res: LaunchResult; t: number }) {
  const f = frameAt(res.frames, t);
  const tiles: Array<[string, string, string]> = [
    ["T+", fmt(t, 1), "s"], ["Phase", res.phases[f.phase], ""], ["Altitude AGL", fmt(f.up), "m"], ["Vertical speed", fmt(f.vz, 1), "m/s"],
    ["Speed", fmt(f.speed, 1), "m/s"], ["Mach", fmt(f.mach, 2), ""], ["Axial accel", fmt(f.g, 1), "g"],
    ["Downrange", fmt(Math.hypot(f.east, f.north)), "m"], ["Wind here", fmt(Math.hypot(f.wind_e, f.wind_n), 1), "m/s"],
  ];
  return (
    <section className="tiles readouts">
      {tiles.map(([l, v, u]) => <div className="tile" key={l}><div className="label">{l}</div><div className="value">{v}<span className="unit">{u}</span></div></div>)}
    </section>
  );
}

function MiniMap({ res, t }: { res: LaunchResult; t: number }) {
  const S = 260;
  const f = res.frames;
  const ext = Math.max(50, ...f.east.map(Math.abs), ...f.north.map(Math.abs)) * 1.15;
  const x = (e: number) => S / 2 + (e / ext) * (S / 2 - 12);
  const y = (n: number) => S / 2 - (n / ext) * (S / 2 - 12);
  const upto = f.t.findIndex((tt) => tt > t);
  const n = upto < 0 ? f.t.length : upto;
  const pts: string[] = [];
  for (let i = 0; i < n; i += 4) pts.push(`${x(f.east[i]).toFixed(1)},${y(f.north[i]).toFixed(1)}`);
  const cur = frameAt(f, t);
  return (
    <Card title="Top view">
      <svg viewBox={`0 0 ${S} ${S}`} width="100%" role="img" aria-label="Ground track, top view">
        <line className="gridline" x1={S / 2} x2={S / 2} y1={8} y2={S - 8} /><line className="gridline" x1={8} x2={S - 8} y1={S / 2} y2={S / 2} />
        <text x={S / 2 + 4} y={16}>N</text><text x={S - 18} y={S / 2 - 4}>E</text>
        {pts.length > 1 && <polyline className="track-line" points={pts.join(" ")} />}
        <path className="track-pad" d={`M${x(0)},${y(0) - 6}l5,9h-10z`} />
        <circle className="track-now" cx={x(cur.east)} cy={y(cur.north)} r={5} />
      </svg>
      <div className="note">Scale ±{fmt(ext)} m</div>
    </Card>
  );
}

function Results({ res }: { res: LaunchResult }) {
  const s = res.summary, c = res.calm_summary;
  const dist = (x: Record<string, number | null>) => Math.hypot(x.landing_east_m ?? 0, x.landing_north_m ?? 0);
  const bearing = (x: Record<string, number | null>) => ((Math.atan2(x.landing_east_m ?? 0, x.landing_north_m ?? 0) * 180) / Math.PI + 360) % 360;
  const rows: Array<[string, number | null, number | null, string, number]> = [
    ["Apogee (AGL)", s.apogee_agl_m, c.apogee_agl_m, "m", 0], ["Max speed", s.max_velocity_mps, c.max_velocity_mps, "m/s", 1],
    ["Max Mach", s.max_mach, c.max_mach, "", 2], ["Max axial accel", (s.max_axial_accel_mps2 ?? 0) / 9.80665, (c.max_axial_accel_mps2 ?? 0) / 9.80665, "g", 1],
    ["Rail exit speed", s.rail_exit_velocity_mps, c.rail_exit_velocity_mps, "m/s", 1], ["Time to apogee", s.time_to_apogee_s, c.time_to_apogee_s, "s", 1],
    ["Flight time", s.flight_time_s, c.flight_time_s, "s", 0], ["Landing distance", dist(s), dist(c), "m", 0],
    ["Min static margin", s.min_stability_margin_cal, c.min_stability_margin_cal, "cal", 2],
  ];
  return (
    <Card title="Simulated results">
      <table>
        <thead><tr><th>Quantity</th><th>This weather</th><th>Standard calm day</th><th>Weather effect</th></tr></thead>
        <tbody>{rows.map(([l, a, b, u, d]) => (
          <tr key={l}><td>{l}{u && ` (${u})`}</td><td>{a == null ? "—" : fmt(a, d)}</td><td>{b == null ? "—" : fmt(b, d)}</td>
            <td>{a == null || b == null ? "—" : `${a - b >= 0 ? "+" : ""}${fmt(a - b, d)}`}</td></tr>))}
        </tbody>
      </table>
      {res.landing && <div className="note">Pad {fmtCoords(res.site.latitude, res.site.longitude)} at {fmt(res.site.altitude_msl)} m ·
        predicted landing <strong>{fmtCoords(res.landing.latitude, res.landing.longitude)}</strong>
        {" "}(<a href={`geo:${res.landing.latitude.toFixed(6)},${res.landing.longitude.toFixed(6)}`}>open in maps</a>)</div>}
      <div className="note">Landing bearing {fmt(bearing(s))}° from the pad. Events: {res.events.map(([te, e]) => `${evLabel(e).toLowerCase()} ${fmt(te, 1)} s`).join(" · ")}.
        {" "}Motor {String(res.motor.designation)} ({String(res.motor.data_quality)}). All values SIMULATED.</div>
    </Card>
  );
}
