import { useEffect, useMemo, useState } from "react";

import { api, type Mission } from "../api";
import { DataTable } from "../components/DataTable";
import { LineChart, type Marker } from "../components/LineChart";
import { fmt } from "../components/scale";
import { SensorHealth } from "../components/SensorHealth";
import { TrackMap } from "../components/TrackMap";
import { Card, ErrorBox, NumberField, SelectField, Stat, StatusPill, TextField } from "../components/ui";
import { GroundStation, gnssQuality, type DescentPlan } from "../station";

interface SessionStatus { active: boolean; source?: string; simulated?: boolean; flight_id?: string | null;
  plan?: DescentPlan | null; error?: string | null; bytes_received?: number;
  last?: { capture: string; flight_file?: { name: string; sha256: string }; note?: string } }

const b64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

export function GroundPage() {
  const [status, setStatus] = useState<SessionStatus>({ active: false });
  const [flights, setFlights] = useState<Array<{ flight_id: string; mission_id: string | null }>>([]);
  const [missions, setMissions] = useState<Mission[]>([]);
  const [src, setSrc] = useState({ type: "serial", device: "/dev/ttyUSB0", baud: 57600, port: 5600,
                                   mission_id: "", scenario: "nominal", speed: 1 });
  const [flightId, setFlightId] = useState("");
  const [plan, setPlan] = useState<DescentPlan>({ mainDeployAltAgl: 150, mainDescentRate: 5 });
  const [err, setErr] = useState<string | null>(null);
  const [epoch, setEpoch] = useState(0);

  const refresh = () => api.get<SessionStatus>("/api/ground/status").then(setStatus);
  useEffect(() => {
    refresh();
    api.get<Array<{ flight_id: string; mission_id: string | null }>>("/api/flights").then(setFlights);
    api.get<Mission[]>("/api/missions").then((m) => { setMissions(m); if (m.length) setSrc((s) => ({ ...s, mission_id: m[0].id })); });
    const iv = setInterval(refresh, 2000);
    return () => clearInterval(iv);
  }, []);

  const start = async () => {
    setErr(null);
    const source = src.type === "serial" ? { type: "serial", device: src.device, baud: src.baud }
      : src.type === "udp" ? { type: "udp", port: src.port }
      : { type: "sil", mission_id: src.mission_id, scenario: src.scenario, speed: src.speed };
    try {
      await api.post("/api/ground/start", { source, flight_id: flightId || null, plan });
      setEpoch((e) => e + 1); refresh();
    } catch (e) { setErr((e as Error).message); }
  };
  const stop = async () => { try { await api.post("/api/ground/stop"); refresh(); } catch (e) { setErr((e as Error).message); } };

  return (
    <div className="page stack">
      <div className="toolbar">
        <h1>Ground station</h1>
        {status.active && status.simulated && <span className="badge sim">REHEARSAL · SIMULATED DATA</span>}
        {status.active ? <StatusPill status="OK" label={`session: ${status.source}`} /> : <StatusPill status="INFO" label="no session" />}
        <span className="spacer" />
        {status.active ? <button className="danger" onClick={stop}>Stop &amp; file recording</button> : null}
      </div>
      <ErrorBox error={err ?? status.error ?? null} />
      {!status.active && (
        <Card title="Start a session">
          <div className="form grid-form">
            <SelectField label="Source" value={src.type} onChange={(v) => setSrc({ ...src, type: v })}
                         options={[["serial", "Radio on serial port"], ["udp", "Radio bridge over UDP"], ["sil", "Rehearsal (simulated flight)"]]} />
            {src.type === "serial" && <>
              <TextField label="Device" value={src.device} onChange={(v) => setSrc({ ...src, device: v })} />
              <NumberField label="Baud" value={src.baud} step="1" onChange={(v) => setSrc({ ...src, baud: v ?? 57600 })} />
            </>}
            {src.type === "udp" && <NumberField label="UDP port" value={src.port} step="1" onChange={(v) => setSrc({ ...src, port: v ?? 5600 })} />}
            {src.type === "sil" && <>
              <SelectField label="Mission" value={src.mission_id} onChange={(v) => setSrc({ ...src, mission_id: v })}
                           options={missions.map((m) => [m.id, m.name] as [string, string])} />
              <SelectField label="Scenario" value={src.scenario} onChange={(v) => setSrc({ ...src, scenario: v })}
                           options={["nominal", "baro_failure_boost", "imu_failure_coast", "gnss_loss", "telemetry_loss",
                                     "corrupted_packets", "low_battery", "processor_reset_coast"]} />
              <NumberField label="Replay speed" value={src.speed} onChange={(v) => setSrc({ ...src, speed: v ?? 1 })} />
            </>}
            <SelectField label="Record into flight" value={flightId} onChange={setFlightId}
                         options={[["", "— (capture only)"], ...flights.map((f) => [f.flight_id, f.flight_id] as [string, string])]} />
            <NumberField label="Main deploy altitude (plan)" unit="m" value={plan.mainDeployAltAgl} onChange={(v) => setPlan({ ...plan, mainDeployAltAgl: v ?? 150 })} />
            <NumberField label="Main descent rate (plan)" unit="m/s" value={plan.mainDescentRate} onChange={(v) => setPlan({ ...plan, mainDescentRate: v ?? 5 })} />
          </div>
          <button className="primary" onClick={start}>Start</button>
          <div className="note">All received bytes are recorded to an append-only capture with a SHA-256 manifest; on stop the
            capture is filed into the selected flight as raw data (rehearsals are never filed as flight data).</div>
          {status.last && <div className="ok-line">Last session: {status.last.flight_file ? `filed ${status.last.flight_file.name}` : status.last.note ?? `capture ${status.last.capture}`}</div>}
        </Card>
      )}
      {status.active && <GroundLive key={epoch} plan={status.plan ?? plan} />}
    </div>
  );
}

function useStream(plan: DescentPlan | null) {
  const [gs] = useState(() => new GroundStation(null, plan));
  const [, setTick] = useState(0);
  useEffect(() => {
    const es = new EventSource("/api/ground/stream");
    let offset: number | null = null;
    let raf = 0;
    let dirty = false;
    es.onmessage = (ev) => {
      const m = JSON.parse(ev.data) as { t: number; b64: string };
      offset = performance.now() / 1000 - m.t;
      gs.feed(b64(m.b64), m.t);
      if (!dirty) { dirty = true; raf = requestAnimationFrame(() => { dirty = false; setTick((n) => n + 1); }); }
    };
    const clock = setInterval(() => { if (offset !== null) gs.now = performance.now() / 1000 - offset; setTick((n) => n + 1); }, 500);
    return () => { es.close(); cancelAnimationFrame(raf); clearInterval(clock); };
  }, [gs]);
  return gs;
}

/** Pad go/no-go from live telemetry. */
function PadStatus({ gs }: { gs: GroundStation }) {
  const p = gs.latest;
  const h = gs.sensorHealth;
  const q = gnssQuality(p?.gnssFix ?? 0, p?.gnssSats ?? 0);
  const rows: Array<[string, boolean, string]> = p ? [
    ["Telemetry link", gs.linkStatus() === "LINK OK", gs.linkStatus()],
    ["Flight computer armed", gs.state === "ARMED", gs.state],
    ["IMU / baro healthy", !!h && h.imu_accel === "OK" && h.imu_gyro === "OK" && h.baro === "OK",
      h ? `accel ${h.imu_accel}, gyro ${h.imu_gyro}, baro ${h.baro}` : "—"],
    ["Storage healthy", h?.storage === "OK", h?.storage ?? "—"],
    ["Battery", p.batteryMv >= 7400, `${fmt(p.batteryMv / 1000, 2)} V (≥ 7.40 V)`],
    ["GPS fix", q.label === "GOOD" || q.label === "FAIR", `${q.label}, ${p.gnssSats} sats`],
  ] : [];
  const ok = rows.length > 0 && rows.every((r) => r[1]);
  const inFlight = ["ASCENT", "COAST", "DESCENT", "LANDED"].includes(gs.state);
  if (inFlight) {
    return (
      <Card title="Pad status">
        <div className="note">Vehicle has launched ({gs.state}) - pad checks no longer apply.</div>
      </Card>
    );
  }
  return (
    <Card title="Pad status">
      {!p ? <div className="empty">Waiting for telemetry…</div> : (
        <>
          <div className={`banner ${ok ? "go" : "nogo"}`}><StatusPill status={ok ? "GO" : "NO-GO"} label={ok ? "Vehicle reports ready" : "Not ready"} /></div>
          <ul className="health">{rows.map(([n, good, v]) => (
            <li key={n} className={good ? "h-OK" : "h-FAILED"}><span className="icon" aria-hidden="true">{good ? "✓" : "✕"}</span>
              <span className="name">{n}</span><span className="status">{v}</span></li>))}</ul>
          <div className="note">Telemetry-reported status only. The RSO and your checklist decide.</div>
        </>
      )}
    </Card>
  );
}

function GroundLive({ plan }: { plan: DescentPlan | null }) {
  const gs = useStream(plan);
  const [showTable, setShowTable] = useState(false);
  const hist = gs.history;
  const markers: Marker[] = useMemo(() => {
    const out: Marker[] = [];
    for (let i = 1; i < hist.length; i++)
      if (hist[i].state !== hist[i - 1].state && ["ASCENT", "COAST", "DESCENT", "LANDED"].includes(hist[i].state))
        out.push({ t: hist[i].t, label: hist[i].state });
    return out;
  }, [hist.length]);
  const p = gs.latest;
  const q = gnssQuality(p?.gnssFix ?? 0, p?.gnssSats ?? 0);
  const estimate = gs.landingEstimate();
  const track = gs.track.map((pt) => { const [e, n] = gs.en(pt.lat, pt.lon); return { e, n, t: pt.t, alt: pt.alt }; });
  const s = gs.rx.stats;
  return (
    <>
      <div className="linkstats">{gs.linkStatus()} · pkts {s.framesOk} · lost {s.lost} · crc {s.crcFailures} · dup {s.duplicates} · late {s.outOfOrder}
        <button className="link" onClick={() => setShowTable((v) => !v)}>{showTable ? "hide table" : "table view"}</button></div>
      <section className="tiles">
        <Stat label="Flight state" value={gs.state} />
        <Stat label="Altitude (AGL)" value={p ? fmt(p.altitude) : "—"} unit="m" sub={`max ${fmt(gs.maxAltitude)} m`} />
        <Stat label="Vertical velocity" value={p ? fmt(p.velocity, 1) : "—"} unit="m/s" />
        <Stat label="Axial acceleration" value={p && Number.isFinite(p.acceleration) ? fmt(p.acceleration / 9.80665, 1) : "—"} unit="g" />
        <Stat label="Tilt" value={gs.tilt != null ? fmt(gs.tilt) : "—"} unit="°" />
        <Stat label="Battery" value={p ? fmt(p.batteryMv / 1000, 2) : "—"} unit="V" />
        <Stat label="GPS" value={q.label} sub={p ? `${p.gnssSats} sats` : undefined} />
      </section>
      <div className="grid">
        <div className="stack">
          <LineChart title="Altitude" unit="m" data={hist.map((h) => ({ t: h.t, v: h.altitude }))} markers={markers} />
          <LineChart title="Vertical velocity" unit="m/s" digits={1} data={hist.map((h) => ({ t: h.t, v: h.velocity }))} markers={markers} />
          <LineChart title="Axial acceleration" unit="m/s²" digits={1} data={hist.map((h) => ({ t: h.t, v: h.acceleration }))} markers={markers} />
        </div>
        <div className="stack">
          <PadStatus gs={gs} />
          <TrackMap track={track} estimate={estimate} estimateNote={gs.estimateNote} gnssLabel={q.label} gnssSigma={q.sigma} />
          <SensorHealth health={gs.sensorHealth} />
        </div>
      </div>
      {showTable && <DataTable packets={gs.packets} />}
    </>
  );
}
