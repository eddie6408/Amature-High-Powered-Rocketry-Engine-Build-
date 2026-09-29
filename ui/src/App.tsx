import { useEffect, useState } from "react";

import { api } from "./api";
import { Icon, Logo } from "./components/icons";
import { DesignPage } from "./pages/DesignPage";
import { FlightsPage } from "./pages/FlightsPage";
import { GroundPage } from "./pages/GroundPage";
import { LaunchPage } from "./pages/LaunchPage";
import { MissionsPage } from "./pages/MissionsPage";
import { MotorsPage } from "./pages/MotorsPage";
import { ReadinessPage } from "./pages/ReadinessPage";
import { useRoute } from "./router";

const NAV: Array<[string, string, string, string]> = [
  ["home", "Overview", "", "From idea to flight and back"],
  ["design", "Design", "1", "Vehicle, masses, stability"],
  ["motors", "Motors", "2", "Certified thrust data"],
  ["missions", "Simulate", "3", "Missions and Monte Carlo"],
  ["launch", "Launch simulator", "3b", "Animated flight under real weather"],
  ["readiness", "Test & readiness", "4", "Flight-software fault suite, GO / NO-GO"],
  ["ground", "Fly", "5", "Ground station"],
  ["flights", "Analyse", "6", "Flights against prediction"],
];

interface WsStatus { name: string; root: string; vehicles: number; flown_revisions: number; motors: number; missions: number; flights: number }
interface About { version: string; commit: string | null; started_at: string }
interface Ground { active: boolean; simulated?: boolean; source?: string; error?: string }

function savedTheme(): string | null {
  try { return localStorage.getItem("aerodyne-theme"); } catch { return null; }
}

export function App() {
  const route = useRoute();
  const page = route[0] ?? "home";
  const [ws, setWs] = useState<WsStatus | null>(null);
  const [about, setAbout] = useState<About | null>(null);
  const [theme, setTheme] = useState<string>(savedTheme() ?? "dark");
  useEffect(() => { api.get<WsStatus>("/api/workspace").then(setWs).catch(() => setWs(null)); }, [page]);
  useEffect(() => { api.get<About>("/api/about").then(setAbout).catch(() => undefined); }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("aerodyne-theme", theme); } catch { /* private window: theme just isn't remembered */ }
  }, [theme]);
  const current = NAV.find(([id]) => id === page) ?? NAV[0];
  return (
    <div className="shell">
      <nav className="nav" aria-label="Workflow">
        <div className="brand"><span className="logo"><Logo /></span><span>AERODYNE{ws && <small>{ws.name}</small>}</span></div>
        {NAV.map(([id, label, step]) => (
          <a key={id} href={`#/${id}`} className={page === id ? "active" : ""} aria-current={page === id ? "page" : undefined}>
            <Icon name={id} /><span>{label}</span>{step && <span className="step">{step}</span>}
          </a>
        ))}
        <div className="foot">
          <div><span>Version</span><span>{about?.version ?? "—"}</span></div>
          <div><span>Code</span><span>{about?.commit ?? "—"}</span></div>
          <div><span>Up since</span><span>{about ? new Date(about.started_at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—"}</span></div>
          <div><span>Workspace</span><span>{ws ? `${ws.vehicles} veh · ${ws.flights} flt` : "—"}</span></div>
          <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? "Light theme" : "Dark theme"}</button>
        </div>
      </nav>
      <div className="content">
        <StatusBar title={current[1]} sub={current[3]} ws={ws} />
        {page === "home" && <Home ws={ws} />}
        {page === "design" && <DesignPage vehicleId={route[1]} />}
        {page === "motors" && <MotorsPage />}
        {page === "missions" && <MissionsPage missionId={route[1]} />}
        {page === "launch" && <LaunchPage missionId={route[1]} motorKey={route[2]} />}
        {page === "readiness" && <ReadinessPage missionId={route[1]} />}
        {page === "ground" && <GroundPage />}
        {page === "flights" && <FlightsPage flightId={route[1]} />}
      </div>
    </div>
  );
}

function StatusBar({ title, sub, ws }: { title: string; sub: string; ws: WsStatus | null }) {
  const [ground, setGround] = useState<Ground | null>(null);
  const [online, setOnline] = useState(true);
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const poll = () => api.get<Ground>("/api/ground/status").then((g) => { setGround(g); setOnline(true); }).catch(() => setOnline(false));
    poll();
    const a = setInterval(poll, 5000);
    const b = setInterval(() => setNow(new Date()), 1000);
    return () => { clearInterval(a); clearInterval(b); };
  }, []);
  const g = !ground?.active ? { cls: "", label: "Ground station idle" }
    : ground.simulated ? { cls: "warn", label: "Rehearsal running" } : { cls: "live", label: "Telemetry live" };
  return (
    <header className="appbar">
      <div className="title"><strong>{title}</strong><small>{sub}</small></div>
      <span className={`chip ${online ? "ok" : "bad"}`}><span className="dot" />{online ? "Server online" : "Server offline"}</span>
      <span className={`chip ${g.cls}`} title={ground?.source}><span className="dot" />{g.label}</span>
      {ws && <span className="chip ok"><span className="dot" />{ws.name}</span>}
      <span className="clock" aria-label="local time">{now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "short" })}</span>
    </header>
  );
}

function Home({ ws }: { ws: WsStatus | null }) {
  const steps: Array<[string, string, string]> = [
    ["design", "Design the vehicle", "Build it from components (or import OpenRocket), weigh parts as you build and enter measured masses. Stability updates live."],
    ["motors", "Load real motor data", "Import the certified curve for each motor you might fly. Synthetic curves are for learning only and block readiness."],
    ["missions", "Predict the flight", "Pick vehicle revision + motor + site + wind + limits. Run the 6-DOF simulation and a Monte Carlo for dispersion."],
    ["readiness", "Test before building", "Fly the flight software through the fault suite, then get a GO / NO-GO with evidence for every check."],
    ["ground", "Fly it", "Create the flight record, connect the ground radio, watch telemetry and the landing estimate; the capture is filed with the flight."],
    ["flights", "Learn from it", "Import the logs, reconstruct the flight, compare with the prediction, review possible contributors, and revise the design."],
  ];
  return (
    <div className="page stack">
      <p className="lead">From idea to flight and back: every number is labelled MEASURED, SIMULATED, ESTIMATED, DERIVED or
        HYPOTHETICAL, raw flight data is never modified, and flown configurations are locked.</p>
      {ws && (
        <section className="tiles">
          {([["Vehicles", ws.vehicles], ["Flown revisions", ws.flown_revisions], ["Motors", ws.motors], ["Missions", ws.missions], ["Flights", ws.flights]] as const)
            .map(([l, v]) => <div className="tile" key={l}><div className="label">{l}</div><div className="value">{v}</div></div>)}
        </section>
      )}
      <ol className="steps">
        {steps.map(([id, t, d]) => <li key={id}><a href={`#/${id}`}><strong>{t}</strong></a><p>{d}</p></li>)}
      </ol>
      {ws && <div className="note">Workspace: {ws.root}</div>}
    </div>
  );
}
