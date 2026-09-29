import { useEffect, useMemo, useRef, useState } from "react";

import { api, fileToBase64, type Component, type DesignPayload, type Motor, type VehicleSummary } from "../api";
import { Profile, type Shape } from "../components/Profile";
import { fmt } from "../components/scale";
import { Card, ErrorBox, KindBadge, NumberField, SelectField, Stat, StatusPill, TextField } from "../components/ui";
import { go } from "../router";

interface FieldSpec { name: string; label: string; type: string; unit?: string; options?: string[]; optional?: boolean }
interface Schema {
  components: Record<string, { label: string; fields: FieldSpec[]; defaults: Record<string, unknown> }>;
  recovery_device: FieldSpec[];
}
interface Analysis {
  warnings: string[]; profile: Shape[]; length_m?: number; reference_diameter_m?: number;
  mass?: Record<string, { mass_kg: number; cg_m: number; kind: string }>; cp_m?: number; cn_alpha?: number;
  margins_cal?: Record<string, number>; components?: Array<{ name: string; mass_kg: number; cg_m: number; kind: string }>;
}
interface Detail extends VehicleSummary { revision: string; design: DesignPayload }
interface Twin { flights: Array<{ flight_id: string; date: string; revision: string; status: string | null;
  apogee_error_pct: number | null; prediction_basis: string | null }>;
  calibrations: Array<{ revision: string; cd_scale: number; source_flight: string; adopted: string }> }

export function DesignPage({ vehicleId }: { vehicleId?: string }) {
  const [vehicles, setVehicles] = useState<VehicleSummary[]>([]);
  const [schema, setSchema] = useState<Schema | null>(null);
  const [motors, setMotors] = useState<Motor[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const refresh = () => api.get<VehicleSummary[]>("/api/vehicles").then(setVehicles).catch((e) => setError(String(e.message)));
  useEffect(() => {
    refresh();
    api.get<Schema>("/api/schema").then(setSchema);
    api.get<Motor[]>("/api/motors").then(setMotors);
  }, []);
  useEffect(() => {
    if (!vehicleId && vehicles.length) go("design", vehicles[0].vehicle_id);
  }, [vehicleId, vehicles]);

  return (
    <div className="page two-col">
      <aside className="side">
        <Card title="Vehicles" actions={<button onClick={() => setCreating((c) => !c)}>+ New</button>}>
          {creating && <NewVehicle onCreated={(vid) => { setCreating(false); refresh(); go("design", vid); }} />}
          <ul className="list">
            {vehicles.map((v) => (
              <li key={v.vehicle_id} className={v.vehicle_id === vehicleId ? "active" : ""}>
                <a href={`#/design/${v.vehicle_id}`}>
                  <strong>{v.vehicle_id}</strong> {v.name}
                  <span className="muted"> · {v.revisions[v.revisions.length - 1].label}</span>
                </a>
              </li>
            ))}
          </ul>
        </Card>
      </aside>
      <main>
        <ErrorBox error={error} />
        {vehicleId && schema && <Editor key={vehicleId} vehicleId={vehicleId} schema={schema} motors={motors} onSaved={refresh} />}
      </main>
    </div>
  );
}

function NewVehicle({ onCreated }: { onCreated: (vid: string) => void }) {
  const [name, setName] = useState("");
  const [template, setTemplate] = useState("blank");
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const create = async () => {
    setErr(null);
    try {
      const body: Record<string, unknown> = { name: name || "new vehicle", template };
      if (template === "ork" && file) body.ork_b64 = await fileToBase64(file);
      const r = await api.post<{ vehicle_id: string; warnings: string[] }>("/api/vehicles", body);
      if (r.warnings?.length) setWarnings(r.warnings);
      onCreated(r.vehicle_id);
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="form">
      <TextField label="Name" value={name} onChange={setName} />
      <SelectField label="Start from" value={template} onChange={setTemplate}
                   options={[["blank", "Minimal single-deploy"], ["example", "Example 66 mm dual-deploy"], ["ork", "OpenRocket .ork file"]]} />
      {template === "ork" && <input type="file" accept=".ork,.xml" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />}
      <button className="primary" onClick={create} disabled={template === "ork" && !file}>Create</button>
      <ErrorBox error={err} />
      {warnings.map((w) => <div key={w} className="warn-line">{w}</div>)}
    </div>
  );
}

function Editor({ vehicleId, schema, motors, onSaved }: { vehicleId: string; schema: Schema; motors: Motor[]; onSaved: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [design, setDesign] = useState<DesignPayload | null>(null);
  const [newRevision, setNewRevision] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [motorKey, setMotorKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [addType, setAddType] = useState("PointMass");
  const [note, setNote] = useState("");
  const [twin, setTwin] = useState<Twin | null>(null);
  const [cad, setCad] = useState({ density: 1240, axis: "+z", station: 0, length: 0, units: "mm" });
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => { api.get<Twin>(`/api/vehicles/${vehicleId}/twin`).then(setTwin).catch(() => setTwin(null)); }, [vehicleId, detail?.revision]);

  const load = (rev?: string) =>
    api.get<Detail>(`/api/vehicles/${vehicleId}${rev ? `?revision=${rev}` : ""}`).then((d) => {
      setDetail(d); setDesign(structuredClone(d.design)); setDirty(false); setNewRevision(false);
    }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [vehicleId]);
  useEffect(() => { if (!motorKey && motors.length) setMotorKey(motors[0].key); }, [motors, motorKey]);

  // live analysis, debounced
  useEffect(() => {
    if (!design) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      api.post<Analysis>("/api/analyze", { design, motor_key: motorKey || null })
        .then((a) => { setAnalysis(a); setError(null); })
        .catch((e) => setError(e.message));
    }, 250);
  }, [design, motorKey]);

  const rev = detail?.revisions.find((r) => r.label === detail.revision);
  const locked = !!rev && rev.status !== "ACTIVE" && rev.status !== "DRAFT" && !newRevision;
  const update = (fn: (d: DesignPayload) => void) => {
    if (!design || locked) return;
    const d = structuredClone(design);
    fn(d);
    setDesign(d);
    setDirty(true);
  };

  const save = async () => {
    if (!design || !detail) return;
    setError(null); setMsg(null);
    try {
      if (newRevision || rev?.status !== "ACTIVE") {
        if (!note.trim()) { setError("describe the change for the new revision"); return; }
        const r = await api.post<{ revision: string }>(`/api/vehicles/${vehicleId}/revise`, { design, note });
        setMsg(`created ${r.revision}`); setNote("");
        await load(r.revision);
      } else {
        await api.put(`/api/vehicles/${vehicleId}/${detail.revision}`, { design });
        setMsg(`saved ${detail.revision}`); setDirty(false);
      }
      onSaved();
    } catch (e) { setError((e as Error).message); }
  };

  const comps = design?.vehicle.components ?? [];
  const sorted = useMemo(() => comps.map((c, i) => ({ c, i })).sort((a, b) => a.c.x - b.c.x), [comps]);
  if (!detail || !design) return <ErrorBox error={error} />;
  const m = analysis?.mass;
  const margin = analysis?.margins_cal;

  return (
    <div className="stack">
      <div className="toolbar">
        <h1>{detail.vehicle_id} · {detail.name}</h1>
        <SelectField label="Revision" value={detail.revision} onChange={(r) => load(r)}
                     options={detail.revisions.map((r) => [r.label, `${r.label} (${r.status.toLowerCase()})`])} />
        {rev && <StatusPill status={rev.status === "FLOWN" ? "INFO" : rev.status === "ACTIVE" ? "OK" : "WARN"}
                            label={rev.status === "FLOWN" ? `FLOWN · locked (${rev.flights.join(", ")})` : rev.status} />}
        <span className="spacer" />
        {locked && <button onClick={() => setNewRevision(true)}>Edit as new revision</button>}
        {!locked && (
          <>
            {(newRevision || rev?.status !== "ACTIVE") && <TextField label="Change note" value={note} onChange={setNote} />}
            {rev?.status === "ACTIVE" && !newRevision && (
              <button onClick={() => setNewRevision(true)} title="freeze this revision and save changes as the next one">New revision…</button>
            )}
            <button className="primary" disabled={!dirty && !newRevision} onClick={save}>
              {newRevision || rev?.status !== "ACTIVE" ? "Save as new revision" : "Save"}
            </button>
          </>
        )}
      </div>
      <ErrorBox error={error} />
      {msg && <div className="ok-line">{msg}</div>}

      <Card title="Side profile" actions={
        <SelectField label="Motor for analysis" value={motorKey} onChange={setMotorKey}
                     options={motors.map((mo) => [mo.key, `${mo.designation} (${mo.data_quality.toLowerCase()})`])} />}>
        {analysis && <Profile shapes={analysis.profile} length={analysis.length_m ?? 1}
                              cg={m?.FULL?.cg_m ?? m?.EMPTY?.cg_m} cgSpent={m?.MOTOR_SPENT?.cg_m} cp={analysis.cp_m} />}
        <div className="note">Blue = airframe and fins, grey = internal items, darker grey = motor, dashed outline = inner tubes. CG with motor loaded / at burnout; CP from Barrowman (ESTIMATED).</div>
      </Card>

      <section className="tiles">
        <Stat label="Length" value={fmt(analysis?.length_m ?? 0, 3)} unit="m" sub={`Ø ${fmt((analysis?.reference_diameter_m ?? 0) * 1000)} mm`} />
        <Stat label="Liftoff mass" value={m?.FULL ? fmt(m.FULL.mass_kg, 3) : "—"} unit="kg"
              sub={m?.FULL ? <KindBadge kind={m.FULL.kind} /> : "select a motor"} />
        <Stat label="Dry mass (no motor)" value={m?.EMPTY ? fmt(m.EMPTY.mass_kg, 3) : "—"} unit="kg" />
        <Stat label="CP" value={analysis?.cp_m !== undefined ? fmt(analysis.cp_m, 3) : "—"} unit="m" sub="Barrowman, M 0.3" />
        <Stat label="Margin loaded" value={margin?.FULL !== undefined ? fmt(margin.FULL, 2) : "—"} unit="cal"
              sub={margin?.FULL !== undefined ? <StatusPill status={margin.FULL >= 1 ? "PASS" : "FAIL"} label={margin.FULL >= 1 ? "≥ 1 cal" : "< 1 cal"} /> : undefined} />
        <Stat label="Margin at burnout" value={margin?.MOTOR_SPENT !== undefined ? fmt(margin.MOTOR_SPENT, 2) : "—"} unit="cal" />
      </section>
      {analysis?.warnings.map((w) => <div key={w} className="warn-line">⚠ {w}</div>)}
      {design.calibration && (design.calibration as { cd_scale?: number }).cd_scale && (
        <div className="banner warn">
          <StatusPill status="INFO" label="Calibrated from flight" /> drag ×{fmt(Number((design.calibration as Record<string, unknown>).cd_scale), 3)} from
          {" "}{String((design.calibration as Record<string, unknown>).source_flight)} (ESTIMATED) - used by every simulation of this revision.
        </div>
      )}
      {twin && twin.flights.length > 0 && (
        <Card title="Digital twin history">
          <table>
            <thead><tr><th>Flight</th><th>Date</th><th>Revision flown</th><th>Validation</th><th>Apogee error</th><th>Prediction basis</th></tr></thead>
            <tbody>{twin.flights.map((f) => (
              <tr key={f.flight_id}><td><a href={`#/flights/${f.flight_id}`}>{f.flight_id}</a></td><td>{f.date}</td><td>{f.revision}</td>
                <td>{f.status ? <StatusPill status={f.status} /> : "not analysed"}</td>
                <td>{f.apogee_error_pct == null ? "—" : `${f.apogee_error_pct > 0 ? "+" : ""}${fmt(f.apogee_error_pct, 1)} %`}</td>
                <td className="small">{f.prediction_basis ?? "—"}</td></tr>))}
            </tbody>
          </table>
          {twin.calibrations.map((c) => <div key={c.revision} className="note">{c.revision}: drag ×{fmt(c.cd_scale, 3)} adopted from {c.source_flight} ({c.adopted})</div>)}
        </Card>
      )}

      <Card title="Components" actions={
        !locked && (
          <>
            <SelectField label="Add" value={addType} onChange={setAddType}
                         options={Object.entries(schema.components).map(([k, v]) => [k, v.label])} />
            <button onClick={() => update((d) => {
              const last = Math.max(0, ...d.vehicle.components.map((c) => c.x));
              d.vehicle.components.push({ type: addType, x: Math.round(last * 100) / 100,
                ...structuredClone(schema.components[addType].defaults) } as Component);
            })}>Add</button>
          </>
        )}>
        <div className="components">
          {sorted.map(({ c, i }) => {
            const spec = schema.components[c.type];
            const a = analysis?.components?.find((x) => x.name === c.name);
            return (
              <details key={i} className="component">
                <summary>
                  <strong>{c.name}</strong> <span className="muted">{spec?.label ?? c.type} · x = {fmt(c.x, 3)} m</span>
                  {a && <span className="muted"> · {fmt(a.mass_kg * 1000)} g <KindBadge kind={a.kind} /></span>}
                </summary>
                <div className="form grid-form">
                  {spec?.fields.map((f) => <FieldInput key={f.name} f={f} value={c[f.name]} disabled={locked}
                    onChange={(v) => update((d) => { d.vehicle.components[i][f.name] = v; })} />)}
                </div>
                {!locked && (
                  <button className="link danger" onClick={() => {
                    if (window.confirm(`Remove ${c.name}?`)) update((d) => { d.vehicle.components.splice(i, 1); });
                  }}>Remove component</button>
                )}
              </details>
            );
          })}
        </div>
      </Card>

      {!locked && (
        <Card title="Add from CAD (STEP / STL)">
          <div className="note">Exact mass, CG and inertia from the CAD geometry × material density (ESTIMATED until weighed).
            Set the model axis that points aft and the vehicle station of the model origin.</div>
          <div className="form grid-form">
            <NumberField label="Density" unit="kg/m³" value={cad.density} onChange={(v) => setCad({ ...cad, density: v ?? 1240 })} />
            <SelectField label="Model axis pointing aft" value={cad.axis} onChange={(v) => setCad({ ...cad, axis: v })}
                         options={["+z", "-z", "+x", "-x", "+y", "-y"]} />
            <NumberField label="Station of model origin" unit="m" value={cad.station} onChange={(v) => setCad({ ...cad, station: v ?? 0 })} />
            <NumberField label="Part length (drawing)" unit="m" value={cad.length} onChange={(v) => setCad({ ...cad, length: v ?? 0 })} />
            <SelectField label="STL units" value={cad.units} onChange={(v) => setCad({ ...cad, units: v })} options={["mm", "m", "cm", "in"]} />
            <label className="field"><span>CAD file</span>
              <input type="file" accept=".step,.stp,.stl" onChange={async (e) => {
                const f = e.target.files?.[0]; if (!f) return;
                try {
                  const r = await api.post<{ components: Array<Component & { warnings: string[] }> }>("/api/cad/part", {
                    name: f.name.replace(/\.[^.]+$/, ""), filename: f.name, b64: await fileToBase64(f), density: cad.density,
                    axis: cad.axis, nose_station: cad.station, length: cad.length, units: cad.units, merge: true });
                  update((d) => { for (const c of r.components) { const { warnings: _w, kind: _k, ...comp } = c as Component & { warnings: string[]; kind: string }; d.vehicle.components.push(comp as Component); } });
                  setMsg(`added ${r.components.map((c) => c.name).join(", ")}${r.components.flatMap((c) => c.warnings).length ? " - " + r.components.flatMap((c) => c.warnings).join("; ") : ""}`);
                } catch (err) { setError((err as Error).message); }
                e.target.value = "";
              }} /></label>
          </div>
        </Card>
      )}

      <Card title="Recovery" actions={!locked && (
        <button onClick={() => update((d) => {
          d.recovery = d.recovery ?? { body_cd_area: 0.005, devices: [] };
          d.recovery.devices.push({ name: "parachute", cd: 1.5, diameter: 0.9, deploy_event: "apogee",
                                    deploy_altitude_agl: null, delay: 0, inflation_time: 0.8, opening_load_factor: 1.5 });
        })}>Add device</button>)}>
        {!design.recovery?.devices.length && <div className="empty">No recovery devices - readiness will fail.</div>}
        {design.recovery?.devices.map((dev, i) => (
          <div key={i} className="form grid-form device">
            {schema.recovery_device.map((f) => <FieldInput key={f.name} f={f} value={dev[f.name]} disabled={locked}
              onChange={(v) => update((d) => { d.recovery!.devices[i][f.name] = v; })} />)}
            {!locked && <button className="link danger" onClick={() => update((d) => { d.recovery!.devices.splice(i, 1); })}>remove</button>}
          </div>
        ))}
      </Card>
    </div>
  );
}

function FieldInput({ f, value, onChange, disabled }: { f: FieldSpec; value: unknown; onChange: (v: unknown) => void; disabled: boolean }) {
  if (f.type === "number" || f.type === "integer")
    return <NumberField label={f.label} unit={f.unit} optional={f.optional} disabled={disabled}
                        step={f.type === "integer" ? "1" : "any"} value={value as number | null} onChange={onChange} />;
  if (f.type === "select")
    return <SelectField label={f.label} value={String(value ?? "")} options={f.options ?? []} onChange={onChange} disabled={disabled} />;
  if (f.type === "tags")
    return <TextField label={f.label} value={((value as string[]) ?? []).join(", ")} disabled={disabled}
                      onChange={(t) => onChange(t.split(",").map((s) => s.trim()).filter(Boolean))} />;
  return <TextField label={f.label} value={String(value ?? "")} onChange={onChange} disabled={disabled} />;
}
