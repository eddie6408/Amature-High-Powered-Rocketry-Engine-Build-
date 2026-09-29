import { useEffect, useState } from "react";

import { api, fileToBase64, type Mission, type Motor, type Summary, type VehicleSummary } from "../api";
import { LineChart } from "../components/LineChart";
import { fmt } from "../components/scale";
import { Card, ErrorBox, KindBadge, NumberField, SelectField, StatusPill, TextField } from "../components/ui";
import { go } from "../router";

interface RawFile { name: string; sha256: string; bytes: number; imported: string; source: string }
interface Flight { flight_id: string; vehicle_id: string; revision: string; motor_key: string; date: string;
  mission_id: string | null; notes: string; raw_files: RawFile[]; motor: Summary;
  analysis_summary?: { status: string; apogee_agl_m: number };
  analysis?: Analysis | null }
interface Analysis { file: string; status: string; actual_kind: string; actual: Summary; predicted: Summary;
  comparison: Array<{ key: string; label: string; units: string; simulated: number | null; actual: number | null;
    abs_error: number | null; pct_error: number | null }>;
  contributors: Array<{ name: string; evidence: string; suggested_check: string }>; notes: string[];
  actual_series: Record<string, number[]>; predicted_series: Record<string, number[]>; prediction_basis: string;
  motor_quality: string; mapping: Record<string, unknown> }
interface Sniff { columns: string[]; rows: number | null; preview?: string[][]; mapping: Record<string, unknown>;
  warnings: string[]; format?: string; error?: string }

export function FlightsPage({ flightId }: { flightId?: string }) {
  const [flights, setFlights] = useState<Flight[]>([]);
  const load = () => api.get<Flight[]>("/api/flights").then(setFlights);
  useEffect(() => { load(); }, []);
  return (
    <div className="page two-col">
      <aside className="side">
        <Card title="Flights" actions={<button onClick={() => go("flights", "new")}>+ New</button>}>
          <ul className="list">
            {flights.map((f) => (
              <li key={f.flight_id} className={f.flight_id === flightId ? "active" : ""}>
                <a href={`#/flights/${f.flight_id}`}><strong>{f.flight_id}</strong>
                  <span className="muted"> · {f.vehicle_id} {f.revision} · {f.date}</span>
                  {f.analysis_summary && <span className="muted"> · {fmt(f.analysis_summary.apogee_agl_m)} m</span>}</a>
              </li>
            ))}
          </ul>
          {!flights.length && <div className="empty">No flights recorded yet.</div>}
        </Card>
      </aside>
      <main>
        {flightId === "new" ? <NewFlight onCreated={(id) => { load(); go("flights", id); }} />
          : flightId ? <FlightView key={flightId} flightId={flightId} onChanged={load} /> : null}
      </main>
    </div>
  );
}

function NewFlight({ onCreated }: { onCreated: (id: string) => void }) {
  const [vehicles, setVehicles] = useState<VehicleSummary[]>([]);
  const [motors, setMotors] = useState<Motor[]>([]);
  const [missions, setMissions] = useState<Mission[]>([]);
  const [f, setF] = useState({ flight_id: "", vehicle_id: "", revision: "", motor_key: "", mission_id: "",
    date: new Date().toISOString().slice(0, 10), notes: "", hardware_version: "", firmware_version: "", sensor_config: "" });
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.get<VehicleSummary[]>("/api/vehicles").then(setVehicles);
    api.get<Motor[]>("/api/motors").then(setMotors);
    api.get<Mission[]>("/api/missions").then(setMissions);
  }, []);
  const set = (k: keyof typeof f, v: string) => setF((o) => ({ ...o, [k]: v }));
  const veh = vehicles.find((v) => v.vehicle_id === f.vehicle_id);
  const create = async () => {
    setErr(null);
    try {
      const r = await api.post<{ flight_id: string }>("/api/flights", {
        flight_id: f.flight_id, vehicle_id: f.vehicle_id, revision: f.revision, motor_key: f.motor_key,
        mission_id: f.mission_id || null, date: f.date, notes: f.notes,
        hardware: { hardware_version: f.hardware_version || "unspecified", firmware_version: f.firmware_version || "unspecified",
                    sensor_config: f.sensor_config || "unspecified" } });
      onCreated(r.flight_id);
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <Card title="Record a flight">
      <p className="lead">Recording a flight freezes the flown revision forever: later changes become a new revision.
        Create the record on launch day, before connecting the ground station, so telemetry is filed with it.</p>
      <div className="form grid-form">
        <TextField label="Flight ID" value={f.flight_id} onChange={(v) => set("flight_id", v)} />
        <TextField label="Date" value={f.date} onChange={(v) => set("date", v)} />
        <SelectField label="Mission (prediction)" value={f.mission_id} onChange={(v) => {
          const m = missions.find((x) => x.id === v);
          setF((o) => ({ ...o, mission_id: v, ...(m ? { vehicle_id: m.vehicle_id, revision: m.revision, motor_key: m.motor_key } : {}) }));
        }} options={[["", "—"], ...missions.map((m) => [m.id, m.name] as [string, string])]} />
        <SelectField label="Vehicle" value={f.vehicle_id} onChange={(v) => { set("vehicle_id", v); const vv = vehicles.find((q) => q.vehicle_id === v); set("revision", vv ? vv.revisions[vv.revisions.length - 1].label : ""); }}
                     options={[["", "—"], ...vehicles.map((v) => [v.vehicle_id, `${v.vehicle_id} ${v.name}`] as [string, string])]} />
        <SelectField label="Revision flown" value={f.revision} onChange={(v) => set("revision", v)}
                     options={(veh?.revisions ?? []).map((r) => [r.label, `${r.label} (${r.status.toLowerCase()})`] as [string, string])} />
        <SelectField label="Motor flown" value={f.motor_key} onChange={(v) => set("motor_key", v)}
                     options={[["", "—"], ...motors.map((m) => [m.key, `${m.manufacturer} ${m.designation} (${m.data_quality.toLowerCase()})`] as [string, string])]} />
        <TextField label="Flight computer hardware" value={f.hardware_version} onChange={(v) => set("hardware_version", v)} />
        <TextField label="Firmware version" value={f.firmware_version} onChange={(v) => set("firmware_version", v)} />
        <TextField label="Sensor configuration" value={f.sensor_config} onChange={(v) => set("sensor_config", v)} />
        <TextField label="Notes" value={f.notes} onChange={(v) => set("notes", v)} />
      </div>
      <button className="primary" disabled={!f.flight_id || !f.vehicle_id || !f.revision || !f.motor_key} onClick={create}>Create flight record</button>
      <ErrorBox error={err} />
    </Card>
  );
}

const MAP_COLUMNS: Array<[string, string]> = [["time", "Time"], ["altitude", "Altitude"], ["pressure", "Pressure"],
  ["accel", "Axial acceleration"], ["accel_y", "Accel Y"], ["accel_z", "Accel Z"], ["gyro_x", "Gyro X"], ["gyro_y", "Gyro Y"],
  ["gyro_z", "Gyro Z"], ["latitude", "Latitude"], ["longitude", "Longitude"], ["gps_altitude", "GPS altitude"]];
const UNITS: Array<[string, string[]]> = [["time_unit", ["s", "ms", "us"]], ["altitude_unit", ["m", "ft"]],
  ["pressure_unit", ["Pa", "hPa", "mbar", "kPa", "inHg"]], ["accel_unit", ["mps2", "g", "ftps2"]],
  ["gyro_unit", ["rad/s", "deg/s"]], ["gps_altitude_unit", ["m", "ft"]]];

function FlightView({ flightId, onChanged }: { flightId: string; onChanged: () => void }) {
  const [f, setF] = useState<Flight | null>(null);
  const [file, setFile] = useState<string>("");
  const [sn, setSn] = useState<Sniff | null>(null);
  const [mapping, setMapping] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [cal, setCal] = useState<{ proposal: { value: number; note: string } | null; reason: string } | null>(null);
  const load = () => api.get<Flight>(`/api/flights/${flightId}`).then((x) => { setF(x); if (!file && x.raw_files.length) setFile(x.analysis?.file ?? x.raw_files[0].name); });
  useEffect(() => { load(); }, [flightId]);
  useEffect(() => {
    if (!file) return;
    if (file.endsWith(".cap")) { setSn({ columns: [], rows: null, mapping: { format: "telemetry_capture" }, warnings: [], format: "telemetry_capture" }); setMapping({ format: "telemetry_capture" }); return; }
    api.get<Sniff>(`/api/flights/${flightId}/files/${encodeURIComponent(file)}/sniff`).then((s) => {
      setSn(s); setMapping(f?.analysis?.file === file ? f.analysis.mapping : s.mapping);
    }).catch((e) => setErr(e.message));
  }, [file]);
  if (!f) return <ErrorBox error={err} />;
  const a = f.analysis;

  const upload = async (fl: File) => {
    setErr(null);
    try {
      await api.post(`/api/flights/${flightId}/files`, { name: fl.name, b64: await fileToBase64(fl), source: "uploaded" });
      setFile(fl.name.replace(/[^A-Za-z0-9._-]+/g, "_")); await load();
    } catch (e) { setErr((e as Error).message); }
  };
  const analyze = async () => {
    setErr(null); setBusy(true);
    try { await api.post(`/api/flights/${flightId}/analyze`, { file, mapping }); await load(); onChanged(); }
    catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };
  const calibrate = async () => { setBusy(true); try { setCal(await api.post(`/api/flights/${flightId}/calibrate`)); } catch (e) { setErr((e as Error).message); } setBusy(false); };
  const ser = (s: Record<string, number[]>, k: string) => s.t.map((t, i) => ({ t, v: s[k][i] }));

  return (
    <div className="stack">
      <div className="toolbar">
        <h1>Flight {f.flight_id}</h1>
        <span className="muted">{f.vehicle_id} {f.revision} · {String(f.motor.manufacturer)} {String(f.motor.designation)} · {f.date}</span>
        <span className="spacer" />
        <button onClick={async () => {
          const r = await api.get<{ markdown: string }>(`/api/flights/${flightId}/report`);
          const a = document.createElement("a");
          a.href = URL.createObjectURL(new Blob([r.markdown], { type: "text/markdown" }));
          a.download = `${flightId}-report.md`;
          a.click();
          URL.revokeObjectURL(a.href);
        }}>Export report</button>
        <button onClick={() => go("ground")}>Open ground station</button>
      </div>
      <ErrorBox error={err} />
      <Card title="Raw data (write-once, SHA-256 verified)" actions={
        <label className="button">Upload log<input type="file" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></label>}>
        {!f.raw_files.length && <div className="empty">Upload the altimeter / flight-computer log (CSV/TXT), or record telemetry with the ground station.</div>}
        <table>
          <thead><tr><th></th><th>File</th><th>Size</th><th>Imported</th><th>SHA-256</th><th>Source</th></tr></thead>
          <tbody>{f.raw_files.map((r) => (
            <tr key={r.name} className={r.name === file ? "selected" : ""} onClick={() => setFile(r.name)}>
              <td><input type="radio" readOnly checked={r.name === file} /></td><td>{r.name}</td><td>{fmt(r.bytes / 1024, 1)} kB</td>
              <td className="small">{r.imported}</td><td className="mono small">{r.sha256.slice(0, 16)}…</td><td className="small">{r.source}</td>
            </tr>))}
          </tbody>
        </table>
      </Card>
      {file && sn && (
        <Card title="Column mapping" actions={<button className="primary" onClick={analyze} disabled={busy}>{busy ? "Analysing…" : "Analyse flight"}</button>}>
          {sn.format === "telemetry_capture" ? <div className="note">Ground-station telemetry capture: decoded with the TELEMETRY-2 receiver (altitude is the on-board estimate).</div> : (
            <>
              <div className="note">Proposed from the file header - confirm every column and unit. {sn.rows} data rows.</div>
              {sn.warnings.map((w) => <div key={w} className="warn-line">⚠ {w}</div>)}
              <div className="form grid-form">
                {MAP_COLUMNS.map(([k, l]) => (
                  <SelectField key={k} label={l} value={String(mapping[k] ?? "")} onChange={(v) => setMapping((m) => ({ ...m, [k]: v || null }))}
                               options={[["", "—"], ...sn.columns.map((c) => [c, c] as [string, string])]} />
                ))}
                {UNITS.map(([k, opts]) => (
                  <SelectField key={k} label={k.replace("_", " ")} value={String(mapping[k] ?? opts[0])} options={opts}
                               onChange={(v) => setMapping((m) => ({ ...m, [k]: v }))} />
                ))}
                <SelectField label="Accelerometer reads +1 g on the pad" value={String(mapping.accel_includes_gravity ?? true)}
                             options={[["true", "yes (raw sensor)"], ["false", "no (gravity removed)"]]}
                             onChange={(v) => setMapping((m) => ({ ...m, accel_includes_gravity: v === "true" }))} />
                <NumberField label="Accel sign (-1 if axis points aft)" value={Number(mapping.accel_sign ?? 1)} onChange={(v) => setMapping((m) => ({ ...m, accel_sign: v ?? 1 }))} />
              </div>
              {sn.preview && (
                <div className="table-wrap"><table><thead><tr>{sn.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                  <tbody>{sn.preview.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody></table></div>
              )}
            </>
          )}
        </Card>
      )}
      {a && (
        <>
          <div className="section-head"><h2>Simulation vs reality</h2>
            <StatusPill status={a.status} label={`digital twin: ${a.status}`} />
            <span className="muted">actual: <KindBadge kind={a.actual_kind} /> · prediction: {a.prediction_basis}</span></div>
          {a.motor_quality !== "CERTIFIED" && a.motor_quality !== "MANUFACTURER" && a.motor_quality !== "MEASURED" &&
            <div className="warn-line">⚠ prediction used {a.motor_quality} motor data</div>}
          <Card>
            <table>
              <thead><tr><th>Metric</th><th>Simulated</th><th>Actual</th><th>Abs. error</th><th>% error</th></tr></thead>
              <tbody>{a.comparison.map((r) => (
                <tr key={r.key}><td>{r.label} ({r.units})</td><td>{r.simulated === null ? "—" : fmt(r.simulated, 1)}</td>
                  <td>{r.actual === null ? "—" : fmt(r.actual, 1)}</td><td>{r.abs_error === null ? "—" : fmt(r.abs_error, 1)}</td>
                  <td>{r.pct_error === null ? "—" : `${r.pct_error > 0 ? "+" : ""}${fmt(r.pct_error, 1)} %`}</td></tr>))}
              </tbody>
            </table>
          </Card>
          <div className="chart-grid">
            <LineChart title="Altitude" unit="m" series={[
              { name: "Predicted", data: ser(a.predicted_series, "altitude"), slot: 1, dashed: true },
              { name: "Measured", data: ser(a.actual_series, "altitude"), slot: 2 }]} />
            <LineChart title="Vertical velocity" unit="m/s" digits={1} series={[
              { name: "Predicted", data: ser(a.predicted_series, "vz"), slot: 1, dashed: true },
              { name: "Measured", data: ser(a.actual_series, "velocity"), slot: 2 }]} />
          </div>
          <Card title="Possible contributors" actions={<button onClick={calibrate} disabled={busy}>Propose drag calibration</button>}>
            <div className="note">Evidence-based candidates - not causal attributions.</div>
            <ul>{a.contributors.map((c) => <li key={c.name}><strong>{c.name}</strong> — {c.evidence}. <em>Check: {c.suggested_check}</em></li>)}</ul>
            {cal && (cal.proposal ? <div className="ok-line">Proposed Cd scale {fmt(cal.proposal.value, 3)} (ESTIMATED): {cal.reason}</div>
              : <div className="warn-line">{cal.reason}</div>)}
            {a.notes.map((n) => <div key={n} className="note">{n}</div>)}
          </Card>
        </>
      )}
    </div>
  );
}
