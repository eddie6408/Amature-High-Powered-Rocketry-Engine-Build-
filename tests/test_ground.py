import json
import math
import threading
import urllib.request
from pathlib import Path

import pytest

from aerodyne.examples import example_config, example_recovery
from aerodyne.ground import DescentPlan, GroundStation
from aerodyne.ground import fixtures as gfix
from aerodyne.ground import server
from aerodyne.sil.runner import SILRunner
from aerodyne.vehicle.mass import MassPropertiesEngine

FIXTURE = Path(__file__).resolve().parents[1] / "ground-ui" / "src" / "__fixtures__" / "sil_corrupted.json"


@pytest.fixture(scope="module")
def frames(nominal_sim):
    return SILRunner(nominal_sim, []).run().radio_frames


def _plan():
    cfg = example_config()
    mass = MassPropertiesEngine(cfg.vehicle).configuration("MOTOR_SPENT", cfg.motor).mass
    return DescentPlan.from_recovery(example_recovery(), mass)


@pytest.mark.parametrize("plan", [True, False])
def test_landing_estimate_contains_truth(nominal_sim, frames, plan):
    true_e, true_n = nominal_sim.position[-1, :2]
    gs = GroundStation(descent_plan=_plan() if plan else None)
    n = 0
    for t, data in frames:
        gs.feed(data, t)
        est = gs.landing_estimate()
        if est:
            n += 1
            miss = math.hypot(est["east_m"] - true_e, est["north_m"] - true_n)
            assert miss <= est["radius_m"], (gs.status.altitude_m, est)
            assert est["kind"] == "ESTIMATED"
    assert n > 100
    assert gs.status.state == "LANDED"


def test_no_estimate_without_baro_aiding(nominal_sim):
    from aerodyne.sil.faults import STANDARD_SCENARIOS

    r = SILRunner(nominal_sim, STANDARD_SCENARIOS["baro_failure_boost"]).run()
    gs = GroundStation(descent_plan=_plan())
    notes = set()
    for t, data in r.radio_frames:
        gs.feed(data, t)
        assert gs.landing_estimate() is None
        notes.add(gs.estimate_note)
    assert "landing estimate unavailable: altitude not baro-aided" in notes


def test_ui_fixture_is_in_sync():
    """The TypeScript tests assert against this file; it must match the Python
    implementation exactly (regenerate with python -m aerodyne.ground.fixtures)."""
    committed = json.loads(FIXTURE.read_text())
    assert committed == json.loads(json.dumps(gfix.build(), separators=(",", ":")))


def test_capture_roundtrip(tmp_path):
    cap = tmp_path / "cap.bin"
    b = server.Broadcaster(capture=cap)
    b.publish(b"\xae\xd1abc", 1.0)
    b.publish(b"xyz", 2.5)
    manifest = b.close()
    assert server.read_capture(cap) == [(1.0, b"\xae\xd1abc"), (2.5, b"xyz")]
    assert len(manifest["sha256"]) == 64
    assert Path(str(cap) + ".manifest.json").exists()


def test_http_stream_and_info(frames):
    from http.server import ThreadingHTTPServer

    b = server.Broadcaster()
    for t, d in frames[:3]:
        b.publish(d, t)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(b, {"source": "test"}))
    httpd.daemon_threads = True
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    port = httpd.server_address[1]
    try:
        info = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/info", timeout=5).read())
        assert info == {"source": "test"}
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stream", timeout=5)
        events = []
        while len(events) < 3:
            line = resp.readline().decode()
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        import base64

        assert base64.b64decode(events[0]["b64"]) == frames[0][1]
        resp.close()
    finally:
        httpd.shutdown()
