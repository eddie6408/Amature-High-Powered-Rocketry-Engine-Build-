import { useEffect, useState } from "react";

import { api, type Motor } from "../api";
import { go } from "../router";
import { LineChart } from "../components/LineChart";
import { fmt } from "../components/scale";
import { Card, ErrorBox, KindBadge, NumberField, SelectField, TextField } from "../components/ui";

interface Curve extends Motor { time: number[]; thrust: number[]; notes: string }
const QUALITIES = ["CERTIFIED", "MANUFACTURER", "MEASURED", "ESTIMATED", "UNKNOWN"];

interface TestResult { summary: Record<string, number | string>; warnings: string[]; columns: string[];
  curve: { t: number[]; thrust: number[] }; raw: { t: number[]; force: number[] }; sha256: string; saved_key: string | null }

/** Static-test characterization of a commercial/certified motor: raw load-cell log in, measured curve out. */
function StaticTest({ onSaved }: { onSaved: (key: string) => void }) {
  const [text, setText] = useState<string | null>(null);
  const [cols, setCols] = useState<string[]>([]);
  const [p, setP] = useState({ time_col: "", force_col: "", time_unit: "s", force_unit: "N", calibration_uncertainty: 0.01,
    manufacturer: "", designation: "", total_mass: 0, propellant_mass: 0, source_date: "", test_id: "", certification: "" });
  const [res, setRes] = useState<TestResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = async (save: boolean) => {
    setErr(null);
    try {
      const r = await api.post<TestResult>("/api/motors/static-test", { ...p, text, save,
        propellant_mass: p.propellant_mass || null });
      setRes(r);
      if (save && r.saved_key) onSaved(r.saved_key);
    } catch (e) { setErr((e as Error).message); }
  };
  const set = (k: keyof typeof p, v: string | number) => setP((o) => ({ ...o, [k]: v }));
  return (
    <Card title="Characterize a static test (MEASURED)">
      <div className="note">For properly conducted tests of commercial / certified motors: the raw load-cell log is analysed
        (tare, burn window, impulse ± uncertainty) and can be stored as a MEASURED dataset next to the certified curve.</div>
      <div className="form grid-form">
        <label className="field"><span>Load-cell log (CSV)</span>
          <input type="file" accept=".csv,.txt,.tsv" onChange={async (e) => {
            const f = e.target.files?.[0]; if (!f) return;
            const t = await f.text(); setText(t);
            const header = t.split(/\r?\n/).find((l) => l.trim() && !/^[#;]/.test(l.trim()) && /[a-zA-Z]/.test(l)) ?? "";
            const c = header.split(/[,;\t]/).map((s) => s.trim()).filter(Boolean);
            setCols(c);
            setP((o) => ({ ...o, time_col: c.find((x) => /time|^t\b/i.test(x)) ?? c[0] ?? "", force_col: c.find((x) => /force|thrust|load|lbf|\bn\b/i.test(x)) ?? c[1] ?? "",
              time_unit: /ms/i.test(c.find((x) => /time/i.test(x)) ?? "") ? "ms" : "s",
              force_unit: /lbf/i.test(c.join(" ")) ? "lbf" : /kgf/i.test(c.join(" ")) ? "kgf" : "N", test_id: f.name }));
          }} /></label>
        {cols.length > 0 && <>
          <SelectField label="Time column" value={p.time_col} options={cols} onChange={(v) => set("time_col", v)} />
          <SelectField label="Time unit" value={p.time_unit} options={["s", "ms", "us"]} onChange={(v) => set("time_unit", v)} />
          <SelectField label="Force column" value={p.force_col} options={cols} onChange={(v) => set("force_col", v)} />
          <SelectField label="Force unit" value={p.force_unit} options={["N", "lbf", "kgf"]} onChange={(v) => set("force_unit", v)} />
          <NumberField label="Load-cell calibration uncertainty (1σ, fraction)" value={p.calibration_uncertainty} onChange={(v) => set("calibration_uncertainty", v ?? 0.01)} />
        </>}
      </div>
      {cols.length > 0 && <button className="primary" onClick={() => run(false)}>Analyse</button>}
      <ErrorBox error={err} />
      {res && (
        <>
          <section className="tiles">
            <div className="tile"><div className="label">Total impulse</div><div className="value">{fmt(Number(res.summary.total_impulse_Ns), 1)}<span className="unit">N·s</span></div>
              <div className="sub">± {fmt(Number(res.summary.total_impulse_sigma_Ns), 1)} (1σ) · <KindBadge kind="DERIVED" /></div></div>
            <div className="tile"><div className="label">Burn time</div><div className="value">{fmt(Number(res.summary.burn_time_s), 2)}<span className="unit">s</span></div></div>
            <div className="tile"><div className="label">Average / peak</div><div className="value">{fmt(Number(res.summary.average_thrust_N))}<span className="unit">N</span></div>
              <div className="sub">peak {fmt(Number(res.summary.peak_thrust_N))} N</div></div>
            <div className="tile"><div className="label">Tare / noise</div><div className="value">{fmt(Number(res.summary.baseline_N), 2)}<span className="unit">N</span></div>
              <div className="sub">σ {fmt(Number(res.summary.noise_sigma_N), 2)} N</div></div>
          </section>
          {res.warnings.map((w) => <div key={w} className="warn-line">⚠ {w}</div>)}
          <LineChart title="Thrust (tare-corrected, burn window)" unit="N" digits={1} series={[
            { name: "Raw (MEASURED)", data: res.raw.t.map((t, i) => ({ t, v: res.raw.force[i] })), slot: 1 },
            { name: "Analysed (DERIVED)", data: res.curve.t.map((t, i) => ({ t, v: res.curve.thrust[i] })), slot: 2 }]} />
          <div className="form grid-form">
            <TextField label="Manufacturer" value={p.manufacturer} onChange={(v) => set("manufacturer", v)} />
            <TextField label="Designation" value={p.designation} onChange={(v) => set("designation", v)} />
            <NumberField label="Loaded motor mass" unit="kg" value={p.total_mass} onChange={(v) => set("total_mass", v ?? 0)} />
            <NumberField label="Propellant mass (published)" unit="kg" value={p.propellant_mass} onChange={(v) => set("propellant_mass", v ?? 0)} />
            <TextField label="Test date" value={p.source_date} onChange={(v) => set("source_date", v)} />
            <TextField label="Certification / lot reference" value={p.certification} onChange={(v) => set("certification", v)} />
          </div>
          {res.saved_key ? <div className="ok-line">Saved as MEASURED dataset {res.saved_key}</div>
            : <button onClick={() => run(true)}>Save as MEASURED motor dataset</button>}
          <div className="note">Raw log SHA-256 {res.sha256.slice(0, 16)}… is recorded in the dataset source.</div>
        </>
      )}
    </Card>
  );
}

export function MotorsPage() {
  const [motors, setMotors] = useState<Motor[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [curve, setCurve] = useState<Curve | null>(null);
  const [text, setText] = useState("");
  const [quality, setQuality] = useState("UNKNOWN");
  const [source, setSource] = useState("");
  const [date, setDate] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const load = () => api.get<Motor[]>("/api/motors").then((m) => { setMotors(m); if (!sel && m.length) setSel(m[0].key); });
  useEffect(() => { load(); }, []);
  useEffect(() => { if (sel) api.get<Curve>(`/api/motors/${sel}`).then(setCurve); }, [sel]);

  const importEng = async () => {
    setErr(null);
    try {
      const r = await api.post<{ keys: string[] }>("/api/motors/import", { text, quality, source, source_date: date });
      setText(""); await load(); setSel(r.keys[0]);
    } catch (e) { setErr((e as Error).message); }
  };

  return (
    <div className="page stack">
      <h1>Motors</h1>
      <p className="lead">Propulsion is an input: import the certified or manufacturer thrust curve (RASP <code>.eng</code>,
        e.g. from ThrustCurve.org) for each motor you plan to fly, and declare where the data came from. Datasets for
        the same motor are kept side by side, never merged.</p>
      <div className="two-col">
        <Card title="Database">
          <table>
            <thead><tr><th>Motor</th><th>Class</th><th>I (N·s)</th><th>Burn (s)</th><th>Quality</th></tr></thead>
            <tbody>
              {motors.map((m) => (
                <tr key={m.key} className={m.key === sel ? "selected" : ""} onClick={() => setSel(m.key)}>
                  <td>{m.manufacturer} {m.designation}</td><td>{m.classification}</td>
                  <td>{fmt(m.total_impulse_Ns, 1)}</td><td>{fmt(m.burn_time_s, 2)}</td>
                  <td><KindBadge kind={m.data_quality} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <div className="stack">
          {curve && (
            <>
              <LineChart title={`${curve.designation} thrust`} unit="N" digits={1}
                         data={curve.time.map((t, i) => ({ t, v: curve.thrust[i] }))} />
              <button onClick={() => go("launch", "", curve.key)}>Fly this motor in the launch simulator</button>
              <div className="note">Source: {curve.source} ({curve.source_date}) · <KindBadge kind={curve.data_quality} /> · total mass {fmt(curve.total_mass_kg, 3)} kg
                {curve.notes && <> · {curve.notes}</>}</div>
            </>
          )}
          <StaticTest onSaved={async (k) => { await load(); setSel(k); }} />
          <Card title="Import .eng">
            <div className="form">
              <input type="file" accept=".eng,.txt" onChange={async (e) => { const f = e.target.files?.[0]; if (f) { setText(await f.text()); setSource(f.name); } }} />
              <textarea rows={6} placeholder="or paste RASP .eng text" value={text} onChange={(e) => setText(e.target.value)} />
              <SelectField label="Data quality (declare honestly)" value={quality} onChange={setQuality} options={QUALITIES} />
              <TextField label="Source (URL, cert. reference, test ID)" value={source} onChange={setSource} />
              <TextField label="Source date" value={date} onChange={setDate} />
              <button className="primary" disabled={!text.trim()} onClick={importEng}>Import</button>
              <ErrorBox error={err} />
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
