"""AERODYNE application server: JSON API + the web UI.

Local engineering tool: binds to 127.0.0.1 by default and has no
authentication. Only expose it on a trusted field network (``--host``).
"""

from __future__ import annotations

import base64
import json
import mimetypes
import queue
import re
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from aerodyne.app import services as svc
from aerodyne.app.ground_session import GroundSession
from aerodyne.environment import tiles
from aerodyne.reporting.reports import flight_report
from aerodyne.workspace import Workspace, WorkspaceError

TILE_RE = re.compile(r"^/api/tiles/terrain/(\d+)/(\d+)/(\d+)\.png$")
UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"
MAX_BODY = 64 * 1024 * 1024


class App:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.jobs = svc.JobManager()
        self.ground = GroundSession(ws)
        self.routes: list[tuple[str, re.Pattern, Callable[..., Any]]] = []
        r = self.route
        ws_ = ws
        r("GET", r"/api/workspace", lambda q, b: ws_.status())
        r("GET", r"/api/dashboard", lambda q, b: svc.dashboard(ws_))
        r("GET", r"/api/about", lambda q, b: svc.about(self.started_at))
        r("GET", r"/api/schema", lambda q, b: svc.get_schema())
        # vehicles / design
        r("GET", r"/api/vehicles", lambda q, b: ws_.list_vehicles())
        r("POST", r"/api/vehicles", lambda q, b: svc.create_vehicle(
            ws_, b.get("name") or "new vehicle", b.get("template", "blank"), b.get("ork_b64"),
            b.get("author", "unknown")))
        r("GET", r"/api/vehicles/(?P<vid>[^/]+)", lambda q, b, vid: svc.vehicle_detail(
            ws_, vid, q.get("revision")))
        r("PUT", r"/api/vehicles/(?P<vid>[^/]+)/(?P<rev>REV-[A-Z]+)", lambda q, b, vid, rev:
          ws_.save_design(vid, rev, svc.parse_design(b["design"])))
        r("POST", r"/api/vehicles/(?P<vid>[^/]+)/revise", lambda q, b, vid: ws_.revise(
            vid, svc.parse_design(b["design"]), b.get("note", ""), b.get("author", "unknown")))
        r("POST", r"/api/analyze", lambda q, b: svc.analyze_design(ws_, b["design"], b.get("motor_key")))
        # motors
        r("GET", r"/api/motors", lambda q, b: svc.list_motors(ws_))
        r("GET", r"/api/motors/(?P<key>[0-9a-f]+)", lambda q, b, key: svc.motor_curve(ws_, key))
        r("POST", r"/api/motors/static-test", lambda q, b: svc.static_test(ws_, b))
        r("POST", r"/api/wind/parse", lambda q, b: svc.parse_wind(b))
        r("POST", r"/api/motors/import", lambda q, b: svc.import_motor(
            ws_, b["text"], b.get("quality", "UNKNOWN"), b.get("source", ""), b.get("source_date", "")))
        r("POST", r"/api/motors/thrustcurve/search", lambda q, b: svc.thrustcurve_search(b))
        r("POST", r"/api/motors/thrustcurve/import", lambda q, b: svc.thrustcurve_import(ws_, str(b.get("motor_id", ""))))
        # missions and runs
        r("GET", r"/api/missions", lambda q, b: [m.to_dict() for m in ws_.list_missions()])
        r("POST", r"/api/missions", lambda q, b: svc.save_mission(ws_, b))
        r("GET", r"/api/missions/(?P<mid>[^/]+)", lambda q, b, mid: ws_.mission(mid).to_dict())
        r("POST", r"/api/missions/(?P<mid>[^/]+)/simulate", lambda q, b, mid: svc.simulate(ws_, mid))
        r("POST", r"/api/missions/(?P<mid>[^/]+)/montecarlo", lambda q, b, mid: {"job": self.jobs.start(
            "montecarlo", lambda p: svc.monte_carlo(ws_, mid, int(b.get("n", 100)), p))})
        r("POST", r"/api/missions/(?P<mid>[^/]+)/sil", lambda q, b, mid: {"job": self.jobs.start(
            "sil", lambda p: svc.sil_suite(ws_, mid, b.get("backend", "python"), p))})
        r("GET", r"/api/missions/(?P<mid>[^/]+)/pad", lambda q, b, mid: svc.launch_pad(ws_, mid))
        r("POST", r"/api/missions/(?P<mid>[^/]+)/launch", lambda q, b, mid: svc.launch_simulation(
            ws_, mid, b.get("weather", {}), b.get("motor_key"), int(b.get("seed", 1)), location=b.get("location")))
        r("POST", r"/api/tiles/terrain/prefetch", lambda q, b: {"job": self.jobs.start(
            "terrain", lambda p: {"summary": tiles.prefetch(float(b["lat"]), float(b["lon"]), float(b.get("radius_km", 5)),
                                                            int(b.get("max_zoom", 14)), p)})})
        r("GET", r"/api/profile", lambda q, b: ws_.profile())
        r("PUT", r"/api/profile", lambda q, b: ws_.save_profile(b))
        r("POST", r"/api/missions/(?P<mid>[^/]+)/flight-card", lambda q, b, mid: svc.flight_card(ws_, mid, b.get("conditions")))
        r("GET", r"/api/weather/live", lambda q, b: svc.live_weather(float(q["lat"]), float(q["lon"])))
        r("POST", r"/api/missions/(?P<mid>[^/]+)/readiness", lambda q, b, mid: svc.readiness(ws_, mid))
        r("GET", r"/api/runs", lambda q, b: ws_.list_runs(q.get("mission"), q.get("kind")))
        r("GET", r"/api/runs/(?P<rid>[^/]+)", lambda q, b, rid: ws_.run(rid))
        r("GET", r"/api/jobs/(?P<jid>[^/]+)", lambda q, b, jid: self.jobs.get(jid))
        # flights
        r("GET", r"/api/flights", lambda q, b: ws_.list_flights())
        r("POST", r"/api/flights", lambda q, b: svc.create_flight(ws_, b))
        r("GET", r"/api/flights/(?P<fid>[^/]+)", lambda q, b, fid: {
            **ws_.flight(fid), "analysis": ws_.flight_analysis(fid)})
        r("POST", r"/api/flights/(?P<fid>[^/]+)/files", lambda q, b, fid: svc.upload_flight_file(
            ws_, fid, b["name"], b["b64"], b.get("source", "")))
        r("GET", r"/api/flights/(?P<fid>[^/]+)/files/(?P<name>[^/]+)/sniff", lambda q, b, fid, name:
          svc.sniff_flight_file(ws_, fid, name))
        r("POST", r"/api/flights/(?P<fid>[^/]+)/analyze", lambda q, b, fid: svc.analyze_flight(
            ws_, fid, b["file"], b.get("mapping", {})))
        r("GET", r"/api/flights/(?P<fid>[^/]+)/report", lambda q, b, fid: {"markdown": flight_report(
            ws_.flight(fid), ws_.flight_analysis(fid))})
        r("POST", r"/api/flights/(?P<fid>[^/]+)/adopt", lambda q, b, fid: svc.adopt_calibration(
            ws_, fid, float(b["cd_scale"]), b.get("note", ""), b.get("author", "unknown")))
        r("GET", r"/api/vehicles/(?P<vid>[^/]+)/twin", lambda q, b, vid: svc.twin_history(ws_, vid))
        r("POST", r"/api/cad/part", lambda q, b: svc.cad_part(b))
        r("POST", r"/api/flights/(?P<fid>[^/]+)/calibrate", lambda q, b, fid: svc.propose_calibration(ws_, fid))
        # ground station
        r("GET", r"/api/ground/status", lambda q, b: self.ground.status())
        r("POST", r"/api/ground/start", lambda q, b: self.ground.start(
            b.get("source", {}), b.get("flight_id"), b.get("plan")))
        r("POST", r"/api/ground/stop", lambda q, b: self.ground.stop())
        # legacy ground-only endpoints used by the standalone viewer
        r("GET", r"/api/info", lambda q, b: {"source": self.ground.status().get("source", "no session"),
                                             "simulated": bool(self.ground.status().get("simulated")),
                                             "plan": self.ground.status().get("plan")})

    def route(self, method: str, pattern: str, fn: Callable[..., Any]) -> None:
        self.routes.append((method, re.compile(f"^{pattern}$"), fn))

    def dispatch(self, method: str, path: str, query: dict[str, str], body: dict) -> tuple[int, Any]:
        for m, pat, fn in self.routes:
            mt = pat.match(path)
            if mt and m == method:
                try:
                    return 200, fn(query, body, **mt.groupdict())
                except (svc.BadRequest, WorkspaceError, ValueError) as exc:
                    return 400, {"error": str(exc)}
                except KeyError as exc:
                    return 404, {"error": f"not found: {exc}"}
                except Exception as exc:  # pragma: no cover - reported, never swallowed
                    return 500, {"error": repr(exc), "trace": traceback.format_exc(limit=4)}
        return 404, {"error": f"no route {method} {path}"}


def make_handler(app: App, ui_dir: Path = UI_DIST):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _tile(self, z: int, x: int, y: int) -> None:
            try:
                data, _ = tiles.terrain_tile(z, x, y)
            except (tiles.TileUnavailable, ValueError) as exc:
                return self._send(404, str(exc).encode(), "text/plain")
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=604800")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj, default=str).encode(), "application/json")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("request too large")
            if n == 0:
                return {}
            return json.loads(self.rfile.read(n) or b"{}")

        def _api(self, method: str) -> None:
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                body = self._body() if method in ("POST", "PUT") else {}
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json(400, {"error": f"bad request body: {exc}"})
            code, out = app.dispatch(method, u.path, q, body)
            self._json(code, out)

        def do_POST(self):  # noqa: N802
            self._api("POST")

        def do_PUT(self):  # noqa: N802
            self._api("PUT")

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            if path in ("/api/stream", "/api/ground/stream"):
                return self._stream()
            m = TILE_RE.match(path)
            if m:
                return self._tile(*(int(v) for v in m.groups()))
            if path.startswith("/api/"):
                return self._api("GET")
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            f = (ui_dir / rel).resolve()
            if rel.startswith("cesium/"):                 # bundled 3D globe library: static, cacheable
                if not str(f).startswith(str(ui_dir.resolve())) or not f.is_file():
                    return self._send(404, b"not found", "text/plain")
                body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(str(f))[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(body)
                return None
            if not str(f).startswith(str(ui_dir.resolve())) or not f.is_file():
                if not (ui_dir / "index.html").is_file():
                    return self._send(404, b"UI not built: cd ui && npm install && npm run build",
                                      "text/plain")
                f = ui_dir / "index.html"
            self._send(200, f.read_bytes(), mimetypes.guess_type(str(f))[0] or "application/octet-stream")

        def _stream(self) -> None:
            b = app.ground.broadcaster
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            if b is None:
                self.wfile.write(b": no session\n\n")
                self.wfile.flush()
                return
            q, backlog = b.subscribe()
            try:
                for t, data in backlog:
                    self._event(t, data)
                while app.ground.broadcaster is b:
                    try:
                        t, data = q.get(timeout=1.0)
                        self._event(t, data)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                b.unsubscribe(q)

        def _event(self, t: float, data: bytes) -> None:
            payload = json.dumps({"t": round(t, 4), "b64": base64.b64encode(data).decode()})
            self.wfile.write(f"data: {payload}\n\n".encode())
            self.wfile.flush()

    return Handler


def serve(ws: Workspace, host: str = "127.0.0.1", port: int = 8765) -> None:
    app = App(ws)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    print(f"AERODYNE on http://{host}:{port}  (workspace: {ws.root})")
    if host not in ("127.0.0.1", "localhost"):
        print("WARNING: no authentication - only expose on a trusted network")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if app.ground.info.get("active"):
            app.ground.stop()
        httpd.server_close()
