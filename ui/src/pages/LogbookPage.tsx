import { useEffect, useState } from "react";

import { api, type Motor } from "../api";
import { fmt } from "../components/scale";
import { Card, ErrorBox, NumberField, SelectField, TextField } from "../components/ui";
import { go } from "../router";

interface LogRow { flight_id: string; date: string; vehicle_id: string; revision: string; motor: string; motor_class: string;
  total_impulse_ns: number | null; cert_level: number | null; site: string | null; site_source: string | null;
  apogee_measured_m: number | null; apogee_predicted_m: number | null; outcome: string | null; recovered: boolean | null;
  damage: string; cert_attempt: boolean | null; notes: string; flyer: string }
interface Logbook { flights: LogRow[]; totals: { flights: number; scrubbed: number; success_rate: number | null; total_impulse_ns: number;
  highest_m: number | null; highest_flight: string | null; by_class: Record<string, number>; by_vehicle: Record<string, number>;
  max_cert_level_flown: number | null } }
interface Item { id: string; kind: string; designation: string; manufacturer: string; motor_key: string | null; quantity: number; delays: string;
  lot: string; purchased: string; cost_each: number | null; location: string; notes: string;
  used: Array<{ flight_id: string | null; count: number; date: string }>;
  motor: { classification: string; total_impulse_Ns: number; propellant_mass_kg: number | null } | null; propellant_on_hand_kg: number | null }
interface Inventory { items: Item[]; totals: { motors_on_hand: number; casings: number; value: number; propellant_on_hand_kg: number;
  propellant_unknown: number; used: number }; note: string }

const OUTCOMES = ["", "success", "partial", "failure", "scrubbed"];
const FT = 0.3048;

export function LogbookPage({ tab }: { tab?: string }) {
  const t = tab === "inventory" ? "inventory" : "flights";
  return (
    <div className="page stack">
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={t === "flights"} className={t === "flights" ? "on" : ""} onClick={() => go("logbook", "flights")}>Flight log</button>
        <button role="tab" aria-selected={t === "inventory"} className={t === "inventory" ? "on" : ""} onClick={() => go("logbook", "inventory")}>Motor inventory</button>
      </div>
      {t === "flights" ? <Flights /> : <InventoryView />}
    </div>
  );
}

function Flights() {
  const [lb, setLb] = useState<Logbook | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => api.get<Logbook>("/api/logbook").then(setLb).catch((e) => setErr((e as Error).message));
  useEffect(() => { load(); }, []);
  const save = async (fid: string, patch: Record<string, unknown>) => {
    setErr(null);
    try { await api.put(`/api/flights/${fid}/log`, patch); await load(); } catch (e) { setErr((e as Error).message); }
  };
  if (!lb) return <ErrorBox error={err} />;
  const tt = lb.totals;
  return (
    <>
      <section className="kpis">
        <div className="tile kpi"><div className="label">Flights</div><div className="value">{tt.flights}</div><div className="sub">{tt.scrubbed} scrubbed</div></div>
        <div className="tile kpi"><div className="label">Success rate</div><div className="value">{tt.success_rate === null ? "—" : `${fmt(tt.success_rate * 100)}%`}</div>
          <div className="sub">of flights with an outcome recorded</div></div>
        <div className="tile kpi"><div className="label">Total impulse flown</div><div className="value">{fmt(tt.total_impulse_ns)} N·s</div>
          <div className="sub">{Object.entries(tt.by_class).map(([c, n]) => `${n}× ${c}`).join(" · ") || "—"}</div></div>
        <div className="tile kpi"><div className="label">Highest flight</div><div className="value">{tt.highest_m ? `${fmt(tt.highest_m)} m` : "—"}</div>
          <div className="sub">{tt.highest_m ? `${fmt(tt.highest_m / FT)} ft · ${tt.highest_flight}` : "measured apogee"}</div></div>
        <div className="tile kpi"><div className="label">Highest certification level flown</div><div className="value">{tt.max_cert_level_flown ?? "—"}</div>
          <div className="sub">from motor impulse</div></div>
      </section>
      <ErrorBox error={err} />
      <Card title="Flight log" actions={<button onClick={() => go("flights", "new")}>+ New flight</button>}>
        {lb.flights.length === 0 ? <div className="empty">No flights yet. Create one in Analyse.</div> : (
          <div className="table-scroll">
            <table className="logbook">
              <thead><tr><th>Flight</th><th>Date</th><th>Vehicle</th><th>Motor</th><th>Site</th><th>Apogee</th><th>Outcome</th><th>Recovered</th><th>Cert</th><th>Notes / damage</th></tr></thead>
              <tbody>
                {[...lb.flights].reverse().map((r) => (
                  <tr key={r.flight_id}>
                    <td><a href={`#/flights/${r.flight_id}`} className="mono">{r.flight_id}</a></td>
                    <td>{r.date || "—"}</td><td>{r.vehicle_id} {r.revision}</td>
                    <td>{r.motor} <span className="muted">({r.motor_class}{r.total_impulse_ns ? `, ${fmt(r.total_impulse_ns)} N·s` : ""})</span></td>
                    <td><input defaultValue={r.site_source === "log" ? r.site ?? "" : ""} placeholder={r.site ? `${r.site} (from mission)` : "site"}
                               aria-label={`Site of ${r.flight_id}`} onBlur={(e) => { if (e.target.value !== (r.site_source === "log" ? r.site ?? "" : "")) save(r.flight_id, { site_name: e.target.value }); }} /></td>
                    <td>{r.apogee_measured_m === null ? "—" : `${fmt(r.apogee_measured_m)} m`}
                      {r.apogee_predicted_m !== null && <span className="muted"> / {fmt(r.apogee_predicted_m)} pred.</span>}</td>
                    <td><select value={r.outcome ?? ""} aria-label={`Outcome of ${r.flight_id}`} onChange={(e) => save(r.flight_id, { outcome: e.target.value })}>
                      {OUTCOMES.map((o) => <option key={o} value={o}>{o || "—"}</option>)}</select></td>
                    <td><select value={r.recovered === null ? "" : r.recovered ? "yes" : "no"} aria-label={`Recovered ${r.flight_id}`}
                                onChange={(e) => save(r.flight_id, { recovered: e.target.value === "" ? null : e.target.value === "yes" })}>
                      <option value="">—</option><option value="yes">yes</option><option value="no">no</option></select></td>
                    <td><label className="check-inline"><input type="checkbox" checked={!!r.cert_attempt} onChange={(e) => save(r.flight_id, { cert_attempt: e.target.checked })} />
                      L{r.cert_level ?? "?"}</label></td>
                    <td><input defaultValue={r.damage || r.notes} placeholder="notes, damage" aria-label={`Notes for ${r.flight_id}`}
                               onBlur={(e) => { if (e.target.value !== (r.damage || r.notes)) save(r.flight_id, { damage: e.target.value }); }} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="note">Outcome, recovery, certification attempt, site and notes are kept beside each flight record; its measured data stays untouched.</div>
      </Card>
    </>
  );
}

const blankItem = { kind: "motor", designation: "", manufacturer: "", motor_key: "", quantity: 1 as number | null, delays: "", lot: "", purchased: "",
  cost_each: null as number | null, location: "", notes: "" };

function InventoryView() {
  const [inv, setInv] = useState<Inventory | null>(null);
  const [motors, setMotors] = useState<Motor[]>([]);
  const [flights, setFlights] = useState<Array<{ flight_id: string }>>([]);
  const [draft, setDraft] = useState<typeof blankItem & { id?: string }>(blankItem);
  const [useFor, setUseFor] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const load = () => api.get<Inventory>("/api/inventory").then(setInv).catch((e) => setErr((e as Error).message));
  useEffect(() => {
    load();
    api.get<Motor[]>("/api/motors").then(setMotors);
    api.get<Array<{ flight_id: string }>>("/api/flights").then(setFlights);
  }, []);
  const save = async () => {
    setErr(null);
    try { await api.post("/api/inventory", { ...draft, motor_key: draft.motor_key || null }); setDraft(blankItem); await load(); }
    catch (e) { setErr((e as Error).message); }
  };
  const use = async (it: Item) => {
    setErr(null);
    try { await api.post(`/api/inventory/${it.id}/use`, { flight_id: useFor[it.id] || null, count: 1 }); await load(); }
    catch (e) { setErr((e as Error).message); }
  };
  const del = async (it: Item) => {
    if (!window.confirm(`Remove ${it.designation} from the inventory?`)) return;
    await api.del(`/api/inventory/${it.id}`); await load();
  };
  if (!inv) return <ErrorBox error={err} />;
  const t = inv.totals;
  return (
    <>
      <section className="kpis">
        <div className="tile kpi"><div className="label">Motors and reloads on hand</div><div className="value">{t.motors_on_hand}</div><div className="sub">{t.used} used so far</div></div>
        <div className="tile kpi"><div className="label">Reusable casings</div><div className="value">{t.casings}</div><div className="sub">hardware</div></div>
        <div className="tile kpi"><div className="label">Propellant on hand</div><div className="value">{fmt(t.propellant_on_hand_kg, 2)} kg</div>
          <div className="sub">{t.propellant_unknown ? `${t.propellant_unknown} item(s) without a linked curve` : "from linked motor data"}</div></div>
        <div className="tile kpi"><div className="label">Inventory value</div><div className="value">${fmt(t.value, 2)}</div><div className="sub">quantity × cost each</div></div>
      </section>
      <ErrorBox error={err} />
      <div className="dash-row">
        <Card title="Stock">
          {inv.items.length === 0 ? <div className="empty">Nothing in stock yet.</div> : (
            <div className="table-scroll">
              <table>
                <thead><tr><th>Item</th><th>Kind</th><th>Qty</th><th>Delays</th><th>Lot</th><th>Location</th><th>Use on flight</th><th /></tr></thead>
                <tbody>
                  {inv.items.map((it) => (
                    <tr key={it.id} className={it.quantity === 0 && it.kind !== "casing" ? "muted-row" : ""}>
                      <td><strong>{it.manufacturer} {it.designation}</strong>
                        {it.motor ? <span className="muted"> · {it.motor.classification}, {fmt(it.motor.total_impulse_Ns)} N·s</span> : <span className="muted"> · no curve linked</span>}</td>
                      <td>{it.kind}</td><td className="mono">{it.quantity}</td><td>{it.delays || "—"}</td><td>{it.lot || "—"}</td><td>{it.location || "—"}</td>
                      <td>{it.kind === "casing" ? <span className="muted">reusable</span> : (
                        <span className="use-cell">
                          <select value={useFor[it.id] ?? ""} aria-label={`Flight for ${it.designation}`} onChange={(e) => setUseFor({ ...useFor, [it.id]: e.target.value })}>
                            <option value="">no flight record</option>{flights.map((f) => <option key={f.flight_id} value={f.flight_id}>{f.flight_id}</option>)}</select>
                          <button onClick={() => use(it)} disabled={it.quantity < 1}>Use 1</button>
                        </span>)}</td>
                      <td className="row-actions"><button className="link" onClick={() => setDraft({ ...blankItem, ...it, motor_key: it.motor_key ?? "", cost_each: it.cost_each })}>edit</button>
                        <button className="link danger" onClick={() => del(it)}>remove</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="note">{inv.note}</div>
        </Card>
        <Card title={draft.id ? "Edit item" : "Add to stock"}>
          <div className="form">
            <SelectField label="Kind" value={draft.kind} onChange={(v) => setDraft({ ...draft, kind: v })}
                         options={[["motor", "single-use motor"], ["reload", "reload kit"], ["casing", "reusable casing"]]} />
            <SelectField label="Thrust curve (optional)" value={draft.motor_key} onChange={(v) => {
              const m = motors.find((x) => x.key === v);
              setDraft({ ...draft, motor_key: v, designation: m ? m.designation : draft.designation, manufacturer: m ? m.manufacturer : draft.manufacturer });
            }} options={[["", "— none —"], ...motors.map((m) => [m.key, `${m.manufacturer} ${m.designation}`] as [string, string])]} />
            <TextField label="Designation" value={draft.designation} onChange={(v) => setDraft({ ...draft, designation: v })} />
            <TextField label="Manufacturer" value={draft.manufacturer} onChange={(v) => setDraft({ ...draft, manufacturer: v })} />
            <NumberField label="Quantity" value={draft.quantity} onChange={(v) => setDraft({ ...draft, quantity: v })} />
            <TextField label="Delays" value={draft.delays} onChange={(v) => setDraft({ ...draft, delays: v })} />
            <TextField label="Lot / batch" value={draft.lot} onChange={(v) => setDraft({ ...draft, lot: v })} />
            <TextField label="Purchased" value={draft.purchased} onChange={(v) => setDraft({ ...draft, purchased: v })} />
            <NumberField label="Cost each" unit="$" optional value={draft.cost_each} onChange={(v) => setDraft({ ...draft, cost_each: v })} />
            <TextField label="Storage location" value={draft.location} onChange={(v) => setDraft({ ...draft, location: v })} />
          </div>
          <div className="presets">
            <button className="primary" disabled={!draft.designation.trim()} onClick={save}>{draft.id ? "Save" : "Add"}</button>
            {draft.id && <button onClick={() => setDraft(blankItem)}>Cancel</button>}
          </div>
        </Card>
      </div>
    </>
  );
}
