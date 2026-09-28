import { useEffect, useState } from "react";

import { api, type Motor } from "../api";
import { LineChart } from "../components/LineChart";
import { fmt } from "../components/scale";
import { Card, ErrorBox, KindBadge, SelectField, TextField } from "../components/ui";

interface Curve extends Motor { time: number[]; thrust: number[]; notes: string }
const QUALITIES = ["CERTIFIED", "MANUFACTURER", "MEASURED", "ESTIMATED", "UNKNOWN"];

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
              <div className="note">Source: {curve.source} ({curve.source_date}) · <KindBadge kind={curve.data_quality} /> · total mass {fmt(curve.total_mass_kg, 3)} kg
                {curve.notes && <> · {curve.notes}</>}</div>
            </>
          )}
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
