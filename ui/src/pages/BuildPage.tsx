import { useEffect, useState } from "react";

import { api } from "../api";
import { fmt } from "../components/scale";
import { Card, ErrorBox, NumberField, SelectField, TextField } from "../components/ui";
import { go } from "../router";

const STATUSES = ["planned", "ordered", "received", "built", "installed"] as const;
interface Part { name: string; type: string; estimated_kg: number; measured_kg: number | null; delta_kg: number | null; status: string;
  cost: number | null; supplier: string; part_number: string; notes: string }
interface Extra { name: string; category: string; cost: number; paid: boolean }
interface Entry { id: string; date: string; title: string; text: string; hours: number; components: string[] }
interface Build { vehicle_id: string; revision: string; parts: Part[]; extras: Extra[]; log: Entry[]; orphan_items: string[];
  budget: { cost?: number | null; currency?: string; dry_mass_kg?: number | null };
  totals: { estimated_dry_kg: number; current_dry_kg: number; weighed: number; parts: number; weighed_share_by_mass: number;
    cost_parts: number; cost_extras: number; cost_total: number; cost_committed: number; parts_by_status: Record<string, number>;
    done_share: number; hours: number } }
interface Vehicle { vehicle_id: string; name: string }

export function BuildPage({ vehicleId }: { vehicleId?: string }) {
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [b, setB] = useState<Build | null>(null);
  const [dirty, setDirty] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [entry, setEntry] = useState({ date: new Date().toISOString().slice(0, 10), hours: 1 as number | null, title: "", text: "" });
  useEffect(() => {
    api.get<Vehicle[]>("/api/vehicles").then((v) => { setVehicles(v); if (!vehicleId && v.length) go("build", v[0].vehicle_id); });
  }, []);
  useEffect(() => {
    setB(null); setDirty(false);
    if (vehicleId) api.get<Build>(`/api/vehicles/${vehicleId}/build`).then(setB).catch((e) => setErr((e as Error).message));
  }, [vehicleId]);
  if (!b) return <div className="page"><ErrorBox error={err} />{!err && <div className="empty">Loading…</div>}</div>;

  const cur = b.budget.currency || "USD";
  const money = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${cur === "USD" ? "$" : ""}${fmt(v, 2)}${cur === "USD" ? "" : ` ${cur}`}`);
  const edit = (fn: (x: Build) => void) => { const c = structuredClone(b); fn(c); setB(c); setDirty(true); };
  const save = async () => {
    setErr(null);
    try {
      const items = Object.fromEntries(b.parts.map((p) => [p.name, { status: p.status, cost: p.cost, supplier: p.supplier, part_number: p.part_number, notes: p.notes }]));
      setB(await api.put<Build>(`/api/vehicles/${b.vehicle_id}/build`, { items, extras: b.extras, budget: b.budget }));
      setDirty(false);
    } catch (e) { setErr((e as Error).message); }
  };
  const addEntry = async () => {
    setErr(null);
    try {
      await api.post(`/api/vehicles/${b.vehicle_id}/build/log`, entry);
      setB(await api.get<Build>(`/api/vehicles/${b.vehicle_id}/build`));
      setEntry({ ...entry, title: "", text: "" });
    } catch (e) { setErr((e as Error).message); }
  };
  const delEntry = async (id: string) => {
    await api.del(`/api/vehicles/${b.vehicle_id}/build/log/${id}`);
    setB(await api.get<Build>(`/api/vehicles/${b.vehicle_id}/build`));
  };
  const t = b.totals;
  const massTarget = b.budget.dry_mass_kg ?? null, costTarget = b.budget.cost ?? null;
  const massTone = massTarget === null ? "" : t.current_dry_kg <= massTarget ? "pos" : "neg";
  const costTone = costTarget === null ? "" : t.cost_total <= costTarget ? "pos" : "neg";

  return (
    <div className="page stack">
      <div className="toolbar">
        <SelectField label="Vehicle" value={b.vehicle_id} onChange={(v) => go("build", v)}
                     options={vehicles.map((v) => [v.vehicle_id, `${v.vehicle_id} · ${v.name}`] as [string, string])} />
        <span className="muted small">parts and masses from {b.revision}</span>
        <div className="spacer" />
        {dirty && <span className="warn-line">unsaved changes</span>}
        <button className="primary" disabled={!dirty} onClick={save}>Save</button>
      </div>
      <ErrorBox error={err} />
      <section className="kpis">
        <div className="tile kpi"><div className="label">Cost</div><div className={`value ${costTone}`}>{money(t.cost_total)}</div>
          <div className="sub">{costTarget !== null ? `of ${money(costTarget)} budget · ` : ""}{money(t.cost_committed)} committed</div></div>
        <div className="tile kpi"><div className="label">Dry mass (as weighed so far)</div><div className={`value ${massTone}`}>{fmt(t.current_dry_kg * 1000)} g</div>
          <div className="sub">{massTarget !== null ? `target ${fmt(massTarget * 1000)} g · ` : ""}estimate {fmt(t.estimated_dry_kg * 1000)} g</div></div>
        <div className="tile kpi"><div className="label">Weighed</div><div className="value">{t.weighed}/{t.parts}</div>
          <div className="sub">{fmt(t.weighed_share_by_mass * 100)}% of the estimated mass</div></div>
        <div className="tile kpi"><div className="label">Build progress</div><div className="value">{fmt(t.done_share * 100)}%</div>
          <div className="sub">{STATUSES.map((s) => `${t.parts_by_status[s] ?? 0} ${s}`).join(" · ")}</div></div>
        <div className="tile kpi"><div className="label">Build time</div><div className="value">{fmt(t.hours, 1)} h</div>
          <div className="sub">{b.log.length} journal entr{b.log.length === 1 ? "y" : "ies"}</div></div>
      </section>

      <div className="dash-row">
        <Card title="Mass budget by part">
          <div className="card-sub">Estimated from the design, and the scale reading once a part is weighed (enter it in Design).</div>
          <MassBars parts={b.parts} />
        </Card>
        <Card title="Budgets">
          <div className="form">
            <NumberField label="Cost budget" unit={cur} optional value={b.budget.cost ?? null} onChange={(v) => edit((x) => { x.budget.cost = v; })} />
            <TextField label="Currency" value={cur} onChange={(v) => edit((x) => { x.budget.currency = v.toUpperCase().slice(0, 3); })} />
            <NumberField label="Dry mass target" unit="kg" optional value={b.budget.dry_mass_kg ?? null} onChange={(v) => edit((x) => { x.budget.dry_mass_kg = v; })} />
          </div>
          <h3>Other costs <span className="muted">(epoxy, paint, hardware, shipping…)</span></h3>
          <table className="extras">
            <colgroup><col style={{ width: "38%" }} /><col style={{ width: "28%" }} /><col style={{ width: "18%" }} /><col style={{ width: "8%" }} /><col /></colgroup>
            <thead><tr><th>Item</th><th>Category</th><th>Cost</th><th>Paid</th><th /></tr></thead>
            <tbody>
              {b.extras.map((x, i) => (
                <tr key={i}>
                  <td><input value={x.name} onChange={(e) => edit((c) => { c.extras[i].name = e.target.value; })} aria-label="Item" /></td>
                  <td><input value={x.category} onChange={(e) => edit((c) => { c.extras[i].category = e.target.value; })} aria-label="Category" /></td>
                  <td><input type="number" step="any" value={x.cost} onChange={(e) => edit((c) => { c.extras[i].cost = Number(e.target.value) || 0; })} aria-label="Cost" /></td>
                  <td><input type="checkbox" checked={x.paid} onChange={(e) => edit((c) => { c.extras[i].paid = e.target.checked; })} aria-label="Paid" /></td>
                  <td><button className="link danger" aria-label={`Remove ${x.name || "cost"}`} title="Remove" onClick={() => edit((c) => { c.extras.splice(i, 1); })}>✕</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <button onClick={() => edit((c) => { c.extras.push({ name: "", category: "consumables", cost: 0, paid: false }); })}>Add cost</button>
        </Card>
      </div>

      <Card title="Parts">
        <div className="table-scroll">
          <table className="parts">
            <thead><tr><th>Part</th><th>Estimated</th><th>Weighed</th><th>Δ</th><th>Status</th><th>Cost</th><th>Supplier</th><th>Part no.</th></tr></thead>
            <tbody>
              {b.parts.map((p, i) => (
                <tr key={p.name}>
                  <td><strong>{p.name}</strong> <span className="muted small">{p.type}</span></td>
                  <td>{fmt(p.estimated_kg * 1000, 1)} g</td>
                  <td>{p.measured_kg === null ? <span className="muted">—</span> : `${fmt(p.measured_kg * 1000, 1)} g`}</td>
                  <td className={p.delta_kg === null ? "" : p.delta_kg > 0 ? "neg" : "pos"}>{p.delta_kg === null ? "" : `${p.delta_kg > 0 ? "+" : ""}${fmt(p.delta_kg * 1000, 1)} g`}</td>
                  <td><select value={p.status} aria-label={`Status of ${p.name}`} onChange={(e) => edit((c) => { c.parts[i].status = e.target.value; })}>
                    {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}</select></td>
                  <td><input type="number" step="any" className="num-in" value={p.cost ?? ""} aria-label={`Cost of ${p.name}`}
                             onChange={(e) => edit((c) => { c.parts[i].cost = e.target.value === "" ? null : Number(e.target.value); })} /></td>
                  <td><input value={p.supplier} aria-label={`Supplier of ${p.name}`} onChange={(e) => edit((c) => { c.parts[i].supplier = e.target.value; })} /></td>
                  <td><input value={p.part_number} aria-label={`Part number of ${p.name}`} onChange={(e) => edit((c) => { c.parts[i].part_number = e.target.value; })} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {b.orphan_items.length > 0 && <div className="note">Build records for parts no longer in {b.revision}: {b.orphan_items.join(", ")} (kept).</div>}
      </Card>

      <Card title="Build journal">
        <div className="form grid-form">
          <TextField label="Date" value={entry.date} onChange={(v) => setEntry({ ...entry, date: v })} />
          <NumberField label="Hours" unit="h" optional value={entry.hours} onChange={(v) => setEntry({ ...entry, hours: v })} />
          <TextField label="Title" value={entry.title} onChange={(v) => setEntry({ ...entry, title: v })} />
        </div>
        <textarea rows={2} className="wide" placeholder="What was done, measurements, lessons…" value={entry.text} onChange={(e) => setEntry({ ...entry, text: e.target.value })} />
        <button className="primary" disabled={!entry.title.trim()} onClick={addEntry}>Add entry</button>
        {b.log.length > 0 && (
          <ol className="journal">
            {[...b.log].reverse().map((e) => (
              <li key={e.id}>
                <span className="j-date mono">{e.date}</span>
                <div><strong>{e.title}</strong>{e.hours ? <span className="muted"> · {fmt(e.hours, 1)} h</span> : null}{e.text && <p>{e.text}</p>}</div>
                <button className="link danger" onClick={() => delEntry(e.id)}>delete</button>
              </li>
            ))}
          </ol>
        )}
      </Card>
    </div>
  );
}

function MassBars({ parts }: { parts: Part[] }) {
  const rows = [...parts].sort((a, b) => Math.max(b.estimated_kg, b.measured_kg ?? 0) - Math.max(a.estimated_kg, a.measured_kg ?? 0)).slice(0, 14);
  const max = Math.max(...rows.map((p) => Math.max(p.estimated_kg, p.measured_kg ?? 0)), 1e-6);
  return (
    <div className="massbars">
      <div className="legend"><span><i className="key" />Estimated</span><span><i className="key s2" />Weighed</span></div>
      {rows.map((p) => (
        <div className="mb-row" key={p.name}>
          <span className="mb-name" title={p.name}>{p.name}</span>
          <div className="mb-track">
            <div className="mb-est" style={{ width: `${(p.estimated_kg / max) * 100}%` }} />
            {p.measured_kg !== null && <div className="mb-meas" style={{ width: `${(p.measured_kg / max) * 100}%` }} />}
          </div>
          <span className="mb-val mono">{fmt((p.measured_kg ?? p.estimated_kg) * 1000)} g</span>
        </div>
      ))}
    </div>
  );
}
