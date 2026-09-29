import { useEffect, useState } from "react";

import { api } from "./api";
import { Icon, Logo } from "./components/icons";
import { BuildPage } from "./pages/BuildPage";
import { DesignPage } from "./pages/DesignPage";
import { FlightsPage } from "./pages/FlightsPage";
import { HomePage } from "./pages/HomePage";
import { GroundPage } from "./pages/GroundPage";
import { LaunchDayPage } from "./pages/LaunchDayPage";
import { LogbookPage } from "./pages/LogbookPage";
import { LaunchPage } from "./pages/LaunchPage";
import { MissionsPage } from "./pages/MissionsPage";
import { MotorsPage } from "./pages/MotorsPage";
import { ReadinessPage } from "./pages/ReadinessPage";
import { RecoveryPage } from "./pages/RecoveryPage";
import { SitesPage } from "./pages/SitesPage";
import { useRoute } from "./router";

const NAV: Array<[string, string, string, string]> = [
  ["home", "Overview", "", "From idea to flight and back"],
  ["design", "Design", "1", "Vehicle, masses, stability"],
  ["build", "Build log", "1b", "Parts, costs, mass budget, journal"],
  ["recovery", "Recovery", "1c", "Parachute sizing, drift, landing energy"],
  ["motors", "Motors", "2", "Certified thrust data"],
  ["missions", "Simulate", "3", "Missions and Monte Carlo"],
  ["launch", "Launch simulator", "3b", "Animated flight under real weather"],
  ["readiness", "Test & readiness", "4", "Flight-software fault suite, GO / NO-GO"],
  ["launchday", "Launch day", "4b", "Safety code, flight card, checklist"],
  ["sites", "Launch sites", "", "Fields, waivers, landing dispersion"],
  ["ground", "Fly", "5", "Ground station"],
  ["flights", "Analyse", "6", "Flights against prediction"],
  ["logbook", "Logbook", "", "Flight log and motor inventory"],
];

interface WsStatus { name: string; root: string; vehicles: number; flown_revisions: number; motors: number; missions: number; flights: number }
interface About { version: string; commit: string | null; started_at: string }

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
        <div className="brand"><span className="logo"><Logo /></span><span>AERODYNE</span></div>
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
        </div>
      </nav>
      <div className="content">
        <StatusBar title={current[1]} sub={current[3]} theme={theme} setTheme={setTheme} />
        {page === "home" && <HomePage />}
        {page === "design" && <DesignPage vehicleId={route[1]} />}
        {page === "build" && <BuildPage vehicleId={route[1]} />}
        {page === "recovery" && <RecoveryPage missionId={route[1]} />}
        {page === "motors" && <MotorsPage />}
        {page === "missions" && <MissionsPage missionId={route[1]} />}
        {page === "launch" && <LaunchPage missionId={route[1]} motorKey={route[2]} />}
        {page === "readiness" && <ReadinessPage missionId={route[1]} />}
        {page === "launchday" && <LaunchDayPage missionId={route[1]} />}
        {page === "sites" && <SitesPage siteId={route[1]} />}
        {page === "ground" && <GroundPage />}
        {page === "flights" && <FlightsPage flightId={route[1]} />}
        {page === "logbook" && <LogbookPage tab={route[1]} />}
      </div>
    </div>
  );
}

function StatusBar({ title, sub, theme, setTheme }: { title: string; sub: string; theme: string; setTheme: (t: string) => void }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <header className="appbar">
      <div className="title"><strong>{title}</strong><small>{sub}</small></div>
      <span className="clock" aria-label="local time">{now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "short" })}</span>
      <div className="theme-switch" role="radiogroup" aria-label="Colour theme">
        <button role="radio" aria-checked={theme === "light"} className={theme === "light" ? "on" : ""} onClick={() => setTheme("light")}
                title="Light mode"><Icon name="sun" size={16} /><span>Light</span></button>
        <button role="radio" aria-checked={theme === "dark"} className={theme === "dark" ? "on" : ""} onClick={() => setTheme("dark")}
                title="Dark mode"><Icon name="moon" size={16} /><span>Dark</span></button>
      </div>
    </header>
  );
}
