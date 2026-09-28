import { useEffect, useMemo, useState } from "react";

import { DataTable } from "./components/DataTable";
import { LineChart, type Marker } from "./components/LineChart";
import { fmt } from "./components/scale";
import { SensorHealth } from "./components/SensorHealth";
import { TrackMap } from "./components/TrackMap";
import { GroundStation, gnssQuality, type DescentPlan } from "./station";

interface Info {
  source: string;
  simulated: boolean;
  plan?: DescentPlan | null;
}

const b64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

function useGroundStation() {
  const [info, setInfo] = useState<Info | null>(null);
  const [gs, setGs] = useState<GroundStation | null>(null);
  const [, setTick] = useState(0);

  useEffect(() => {
    let es: EventSource | null = null;
    let raf = 0;
    let dirty = false;
    let clock: ReturnType<typeof setInterval> | undefined;
    fetch("/api/info")
      .then((r) => r.json())
      .then((i: Info) => {
        setInfo(i);
        const station = new GroundStation(null, i.plan ?? null);
        setGs(station);
        es = new EventSource("/api/stream");
        // server receive-time -> local clock, so link age keeps counting with no packets
        let offset: number | null = null;
        es.onmessage = (ev) => {
          const m = JSON.parse(ev.data) as { t: number; b64: string };
          offset = performance.now() / 1000 - m.t;
          station.feed(b64(m.b64), m.t);
          if (!dirty) {
            dirty = true;
            raf = requestAnimationFrame(() => {
              dirty = false;
              setTick((n) => n + 1);
            });
          }
        };
        clock = setInterval(() => {
          if (offset !== null) station.now = performance.now() / 1000 - offset;
          setTick((n) => n + 1);
        }, 500);
      })
      .catch(() => setInfo({ source: "server unavailable", simulated: false }));
    return () => {
      es?.close();
      cancelAnimationFrame(raf);
      if (clock) clearInterval(clock);
    };
  }, []);
  return { info, gs };
}

function Tile({ label, value, unit, sub, className = "" }: {
  label: string; value: string; unit?: string; sub?: string; className?: string;
}) {
  return (
    <div className={`tile ${className}`}>
      <div className="label">{label}</div>
      <div className="value">{value}{unit && <span className="unit">{unit}</span>}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

function LinkBadge({ status }: { status: string }) {
  const ok = status === "LINK OK";
  return (
    <span className={`badge link ${ok ? "h-OK" : "h-FAILED"}`}>
      <span className="icon" aria-hidden="true">{ok ? "✓" : "✕"}</span> {status}
    </span>
  );
}

export function App() {
  const { info, gs } = useGroundStation();
  const [showTable, setShowTable] = useState(false);
  const [theme, setTheme] = useState<string | null>(null);
  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme;
  }, [theme]);

  const hist = gs?.history ?? [];
  const markers: Marker[] = useMemo(() => {
    const out: Marker[] = [];
    for (let i = 1; i < hist.length; i++) {
      if (hist[i].state !== hist[i - 1].state && ["ASCENT", "COAST", "DESCENT", "LANDED"].includes(hist[i].state)) {
        out.push({ t: hist[i].t, label: hist[i].state });
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hist.length]);

  const p = gs?.latest ?? null;
  const q = gnssQuality(p?.gnssFix ?? 0, p?.gnssSats ?? 0);
  const estimate = gs?.landingEstimate() ?? null;
  const track = (gs?.track ?? []).map((pt) => {
    const [e, n] = gs!.en(pt.lat, pt.lon);
    return { e, n, t: pt.t, alt: pt.alt };
  });
  const s = gs?.rx.stats;
  const tilt = gs?.tilt;

  return (
    <div className="app">
      <header className="topbar">
        <h1>AERODYNE GROUND</h1>
        {info?.simulated && <span className="badge sim" title="Data is from a simulation, not a real vehicle">SIMULATED DATA</span>}
        <span className="src">{info?.source ?? "connecting…"}</span>
        <span className="spacer" />
        <LinkBadge status={gs?.linkStatus() ?? "NO LINK"} />
        {s && (
          <span className="linkstats">
            pkts {s.framesOk} · lost {s.lost} · crc {s.crcFailures} · dup {s.duplicates} · late {s.outOfOrder}
          </span>
        )}
        <button onClick={() => setShowTable((v) => !v)}>{showTable ? "Hide table" : "Table view"}</button>
        <button onClick={() => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark")}>
          Theme
        </button>
      </header>

      <section className="tiles" aria-label="Vehicle status">
        <Tile className="state" label="Flight state" value={gs?.state ?? "NO DATA"} />
        <Tile label="Altitude (AGL)" value={p ? fmt(p.altitude) : "—"} unit="m"
              sub={gs ? `max ${fmt(gs.maxAltitude)} m` : undefined} />
        <Tile label="Vertical velocity" value={p ? fmt(p.velocity, 1) : "—"} unit="m/s" />
        <Tile label="Axial acceleration" value={p && Number.isFinite(p.acceleration) ? fmt(p.acceleration, 1) : "—"} unit="m/s²"
              sub={p && Number.isFinite(p.acceleration) ? `${fmt(p.acceleration / 9.80665, 1)} g` : undefined} />
        <Tile label="Tilt from vertical" value={tilt != null ? fmt(tilt) : "—"} unit="°" />
        <Tile label="Battery" value={p ? fmt(p.batteryMv / 1000, 2) : "—"} unit="V" />
        <Tile label="Temperature" value={p ? fmt(p.temperatureC, 1) : "—"} unit="°C" />
        <Tile label="GPS" value={q.label} sub={p ? `${p.gnssSats} sats${Number.isFinite(q.sigma) ? ` · ±${q.sigma} m` : ""}` : undefined} />
      </section>

      <div className="grid">
        <div className="stack">
          <LineChart title="Altitude" unit="m" data={hist.map((h) => ({ t: h.t, v: h.altitude }))} markers={markers} />
          <LineChart title="Vertical velocity" unit="m/s" digits={1} data={hist.map((h) => ({ t: h.t, v: h.velocity }))} markers={markers} />
          <LineChart title="Axial acceleration" unit="m/s²" digits={1} data={hist.map((h) => ({ t: h.t, v: h.acceleration }))} markers={markers} />
        </div>
        <div className="stack">
          <TrackMap track={track} estimate={estimate} estimateNote={gs?.estimateNote ?? ""}
                    gnssLabel={q.label} gnssSigma={q.sigma} />
          <SensorHealth health={gs?.sensorHealth ?? null} />
        </div>
      </div>
      {showTable && gs && <div style={{ marginTop: 12 }}><DataTable packets={gs.packets} /></div>}
    </div>
  );
}
