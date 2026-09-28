import { useEffect, useState } from "react";

import { api } from "./api";
import { DesignPage } from "./pages/DesignPage";
import { FlightsPage } from "./pages/FlightsPage";
import { GroundPage } from "./pages/GroundPage";
import { MissionsPage } from "./pages/MissionsPage";
import { MotorsPage } from "./pages/MotorsPage";
import { ReadinessPage } from "./pages/ReadinessPage";
import { useRoute } from "./router";

const NAV: Array<[string, string, string]> = [
  ["home", "Overview", ""],
  ["design", "1 · Design", "vehicle, masses, stability"],
  ["motors", "2 · Motors", "certified thrust data"],
  ["missions", "3 · Simulate", "missions, Monte Carlo"],
  ["readiness", "4 · Test & readiness", "SIL suite, go/no-go"],
  ["ground", "5 · Fly", "ground station"],
  ["flights", "6 · Analyse", "flights vs prediction"],
];

interface WsStatus { name: string; root: string; vehicles: number; flown_revisions: number; motors: number; missions: number; flights: number }

export function App() {
  const route = useRoute();
  const page = route[0] ?? "home";
  const [ws, setWs] = useState<WsStatus | null>(null);
  const [theme, setTheme] = useState<string | null>(null);
  useEffect(() => { api.get<WsStatus>("/api/workspace").then(setWs).catch(() => setWs(null)); }, [page]);
  useEffect(() => { if (theme) document.documentElement.dataset.theme = theme; }, [theme]);
  return (
    <div className="shell">
      <nav className="nav" aria-label="Workflow">
        <div className="brand">AERODYNE</div>
        {ws && <div className="ws">{ws.name}</div>}
        {NAV.map(([id, label, sub]) => (
          <a key={id} href={`#/${id}`} className={page === id ? "active" : ""}>
            <span>{label}</span>{sub && <small>{sub}</small>}
          </a>
        ))}
        <button className="theme" onClick={() => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark")}>Theme</button>
      </nav>
      <div className="content">
        {page === "home" && <Home ws={ws} />}
        {page === "design" && <DesignPage vehicleId={route[1]} />}
        {page === "motors" && <MotorsPage />}
        {page === "missions" && <MissionsPage missionId={route[1]} />}
        {page === "readiness" && <ReadinessPage missionId={route[1]} />}
        {page === "ground" && <GroundPage />}
        {page === "flights" && <FlightsPage flightId={route[1]} />}
      </div>
    </div>
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
      <h1>{ws?.name ?? "AERODYNE"}</h1>
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
