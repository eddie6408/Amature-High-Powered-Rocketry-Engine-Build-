"""Live ground-station session: one radio source, one broadcaster, recorded
raw bytes filed into the flight record when the session stops."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aerodyne.ground import server as gserver
from aerodyne.workspace import Workspace, WorkspaceError


class GroundSession:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws
        self.lock = threading.Lock()
        self.broadcaster: gserver.Broadcaster | None = None
        self.stop_event: threading.Event | None = None
        self.thread: threading.Thread | None = None
        self.info: dict[str, Any] = {"active": False}
        self.capture_path: Path | None = None

    def start(self, source: dict[str, Any], flight_id: str | None = None,
              plan: dict[str, float] | None = None) -> dict[str, Any]:
        with self.lock:
            if self.info.get("active"):
                raise WorkspaceError("a ground session is already running - stop it first")
            if flight_id:
                self.ws.flight(flight_id)            # must exist
            kind = source.get("type")
            simulated = False
            if kind == "udp":
                fn = gserver.udp_source(int(source.get("port", 5600)))
                desc = f"UDP :{source.get('port', 5600)}"
            elif kind == "serial":
                fn = gserver.serial_source(source["device"], int(source.get("baud", 57600)))
                desc = f"serial {source['device']} @ {source.get('baud', 57600)}"
            elif kind == "sil":
                from aerodyne.app.services import build_config
                from aerodyne.dynamics.simulator import FlightSimulator
                from aerodyne.sil.faults import STANDARD_SCENARIOS
                from aerodyne.sil.runner import SILRunner

                m = self.ws.mission(source["mission_id"])
                cfg, _ = build_config(self.ws, m)
                scen = source.get("scenario", "nominal")
                frames = SILRunner(FlightSimulator(cfg).run(), STANDARD_SCENARIOS[scen]).run().radio_frames
                fn = gserver.replay_source(frames, float(source.get("speed", 1.0)))
                desc = f"rehearsal: SIL flight of mission '{m.name}' ({scen})"
                simulated = True
            else:
                raise WorkspaceError("source type must be udp, serial or sil")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            cap_dir = self.ws.root / "captures"
            cap_dir.mkdir(exist_ok=True)
            self.capture_path = cap_dir / f"{stamp}-{flight_id or 'unassigned'}.cap"
            self.broadcaster = gserver.Broadcaster(capture=self.capture_path)
            self.stop_event = threading.Event()
            self.thread = threading.Thread(target=self._run, args=(fn,), daemon=True)
            self.info = {"active": True, "source": desc, "simulated": simulated, "flight_id": flight_id,
                         "started": stamp, "plan": plan, "capture": str(self.capture_path), "error": None}
            self.thread.start()
            return dict(self.info)

    def _run(self, fn) -> None:
        try:
            fn(self.broadcaster, self.stop_event)
        except BaseException as exc:  # includes SystemExit from a missing pyserial
            self.info["error"] = str(exc)

    def stop(self) -> dict[str, Any]:
        with self.lock:
            if not self.info.get("active"):
                return {"active": False}
            self.stop_event.set()
            self.thread.join(timeout=3)
            manifest = self.broadcaster.close()
            result = {"active": False, "capture": str(self.capture_path), "manifest": manifest}
            fid = self.info.get("flight_id")
            if fid and not self.info.get("simulated"):
                data = self.capture_path.read_bytes()
                if data:
                    result["flight_file"] = self.ws.add_raw_file(
                        fid, f"telemetry-{self.info['started']}.cap", data,
                        source=f"ground station, {self.info['source']}")
            elif fid and self.info.get("simulated"):
                result["note"] = "rehearsal data is simulated and was not filed into the flight record"
            self.info = {"active": False, "last": result}
            return result

    def status(self) -> dict[str, Any]:
        out = dict(self.info)
        if self.broadcaster is not None and self.info.get("active"):
            out["bytes_received"] = sum(len(d) for _, d in self.broadcaster.history)
        return out
