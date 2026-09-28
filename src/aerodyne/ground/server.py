"""AERODYNE GROUND server: relays raw radio bytes to browser clients.

The server does not decode or filter telemetry - it forwards the exact bytes
received (so the browser's TELEMETRY-2 decoder sees corruption, duplicates and
gaps the way the radio delivered them) and records them to an append-only
capture file with a SHA-256 manifest (raw data is never destroyed).

Endpoints
    GET /api/stream   Server-Sent Events; each event is {"t": rx_time, "b64": raw bytes}
    GET /api/info     source description, capture path
    GET /*            the built web UI (ui/dist)

Sources: SIL replay (for rehearsal/training), UDP (a radio bridge sends raw
bytes as datagrams), serial (optional ``pyserial``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import queue
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterable

UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"


class Broadcaster:
    def __init__(self, capture: Path | None = None, backlog: int = 20000) -> None:
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.history: list[tuple[float, bytes]] = []
        self.backlog = backlog
        self.capture = capture
        self._fh = open(capture, "ab") if capture else None
        self._sha = hashlib.sha256()
        self.t0 = time.monotonic()

    def publish(self, data: bytes, t: float | None = None) -> None:
        t = time.monotonic() - self.t0 if t is None else t
        if self._fh:
            # capture record: f64 time, u32 length, bytes
            import struct

            rec = struct.pack("<dI", t, len(data)) + data
            self._fh.write(rec)
            self._fh.flush()
            self._sha.update(rec)
        with self._lock:
            self.history.append((t, data))
            if len(self.history) > self.backlog:
                del self.history[: len(self.history) - self.backlog]
            for q in self._clients:
                q.put((t, data))

    def subscribe(self) -> tuple[queue.Queue, list[tuple[float, bytes]]]:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._clients.append(q)
            return q, list(self.history)

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def close(self) -> dict[str, str] | None:
        if not self._fh:
            return None
        self._fh.close()
        manifest = {"capture": str(self.capture), "sha256": self._sha.hexdigest(),
                    "format": "repeat{f64 t, u32 n, n bytes} little-endian"}
        Path(str(self.capture) + ".manifest.json").write_text(json.dumps(manifest, indent=2))
        return manifest


def read_capture(path: str | Path) -> list[tuple[float, bytes]]:
    import struct

    data = Path(path).read_bytes()
    out, i = [], 0
    while i + 12 <= len(data):
        t, n = struct.unpack_from("<dI", data, i)
        out.append((t, data[i + 12:i + 12 + n]))
        i += 12 + n
    return out


# ---- sources -------------------------------------------------------------------
def replay_source(frames: Iterable[tuple[float, bytes]], speed: float = 1.0,
                  loop: bool = False) -> Callable[[Broadcaster, threading.Event], None]:
    frames = list(frames)

    def run(b: Broadcaster, stop: threading.Event) -> None:
        while not stop.is_set():
            if not frames:
                return
            t_first = frames[0][0]
            start = time.monotonic()
            for t, data in frames:
                delay = (t - t_first) / speed - (time.monotonic() - start)
                if delay > 0 and stop.wait(delay):
                    return
                b.publish(data)
            if not loop:
                return
            if stop.wait(3.0):
                return
    return run


def sil_replay_frames(scenario: str = "nominal", backend: str = "python") -> list[tuple[float, bytes]]:
    from aerodyne.dynamics.simulator import FlightSimulator
    from aerodyne.examples import example_config
    from aerodyne.sil.faults import STANDARD_SCENARIOS
    from aerodyne.sil.runner import SILRunner

    sim = FlightSimulator(example_config()).run()
    return SILRunner(sim, STANDARD_SCENARIOS[scenario], backend=backend).run().radio_frames


def udp_source(port: int, host: str = "0.0.0.0") -> Callable[[Broadcaster, threading.Event], None]:
    def run(b: Broadcaster, stop: threading.Event) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((host, port))
        s.settimeout(0.5)
        while not stop.is_set():
            try:
                data, _ = s.recvfrom(65535)
            except socket.timeout:
                continue
            b.publish(data)
        s.close()
    return run


def serial_source(device: str, baud: int = 57600) -> Callable[[Broadcaster, threading.Event], None]:
    def run(b: Broadcaster, stop: threading.Event) -> None:
        try:
            import serial  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise SystemExit("serial source needs `pip install pyserial`") from exc
        with serial.Serial(device, baud, timeout=0.2) as port:
            while not stop.is_set():
                data = port.read(512)
                if data:
                    b.publish(data)
    return run


# ---- HTTP ----------------------------------------------------------------------
def make_handler(b: Broadcaster, info: dict, ui_dir: Path = UI_DIST):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            if path == "/api/info":
                return self._send(200, json.dumps(info).encode(), "application/json")
            if path == "/api/stream":
                return self._stream()
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            f = (ui_dir / rel).resolve()
            if not str(f).startswith(str(ui_dir.resolve())) or not f.is_file():
                if not (ui_dir / "index.html").is_file():
                    return self._send(404, b"UI not built: cd ui && npm install && npm run build",
                                      "text/plain")
                f = ui_dir / "index.html"
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            return self._send(200, f.read_bytes(), ctype)

        def _stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q, backlog = b.subscribe()
            try:
                for t, data in backlog:
                    self._event(t, data)
                while True:
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


def serve(source: Callable[[Broadcaster, threading.Event], None], info: dict, port: int = 8765,
          host: str = "127.0.0.1", capture: Path | None = None) -> None:
    b = Broadcaster(capture)
    stop = threading.Event()
    th = threading.Thread(target=source, args=(b, stop), daemon=True)
    th.start()
    httpd = ThreadingHTTPServer((host, port), make_handler(b, info))
    httpd.daemon_threads = True
    print(f"AERODYNE GROUND on http://{host}:{port}  ({info.get('source')})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
        m = b.close()
        if m:
            print(f"capture saved: {m['capture']} sha256={m['sha256']}")
