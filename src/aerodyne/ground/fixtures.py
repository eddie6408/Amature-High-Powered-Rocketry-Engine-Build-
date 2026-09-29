"""Cross-language test fixture for the ground-station implementations.

Produces raw radio bytes (with corruption, duplicates and reordering) from a
SIL run plus the Python GroundStation's results, so the TypeScript UI
(ui/src/*.test.ts) can prove it decodes and estimates identically.

    python -m aerodyne.ground.fixtures ui/src/__fixtures__/sil_corrupted.json
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from aerodyne.ground.station import DescentPlan, GroundStation
from aerodyne.ground.server import sil_replay_frames

PLAN = DescentPlan(main_deploy_alt_agl=150.0, main_descent_rate=3.87)


def _r(x: float | None, nd: int = 6):
    return None if x is None else round(float(x), nd)


def build(scenario: str = "corrupted_packets") -> dict:
    frames = sil_replay_frames(scenario)
    gs = GroundStation(vehicle_id=1, descent_plan=PLAN)
    checkpoints = []
    for i, (t, data) in enumerate(frames):
        gs.feed(data, t)
        if i % 25 == 0 or i == len(frames) - 1:
            e = gs.landing_estimate()
            checkpoints.append({
                "index": i, "state": gs.status.state, "note": gs.estimate_note,
                "estimate": None if e is None else {
                    "eastM": _r(e["east_m"], 3), "northM": _r(e["north_m"], 3),
                    "radiusM": _r(e["radius_m"], 3), "timeToGroundS": _r(e["time_to_ground_s"], 3),
                    "basis": e["basis"]},
            })
    st = gs.rx.stats
    first = gs.history[0]
    return {
        "scenario": scenario,
        "kind": "SIMULATED",
        "plan": {"mainDeployAltAgl": PLAN.main_deploy_alt_agl,
                 "mainDescentRate": PLAN.main_descent_rate},
        "frames": [{"t": t, "b64": base64.b64encode(d).decode()} for t, d in frames],
        "expected": {
            "stats": {"framesOk": st.frames_ok, "crcFailures": st.crc_failures,
                      "duplicates": st.duplicates, "outOfOrder": st.out_of_order,
                      "lost": st.lost, "linkInterruptions": st.link_interruptions},
            "packetsAccepted": len(gs.history),
            "identity": None if gs.rx.identity is None else {
                "firmwareVersion": gs.rx.identity.firmware_version, "commit": gs.rx.identity.commit,
                "hardwareVersion": gs.rx.identity.hardware_version, "firmwareHash": gs.rx.identity.firmware_hash,
                "frames": st.identity_frames},
            "finalState": gs.status.state,
            "maxAltitude": _r(gs.status.max_altitude_m, 3),
            "firstPacket": {"sequence": first.sequence, "altitude": _r(first.altitude, 4),
                            "latitude": _r(first.latitude, 7), "longitude": _r(first.longitude, 7),
                            "batteryMv": first.battery_mv, "sensorStatus": first.sensor_status},
            "checkpoints": checkpoints,
        },
    }


def main(argv: list[str]) -> int:
    out = Path(argv[1] if len(argv) > 1 else "ui/src/__fixtures__/sil_corrupted.json")
    out.write_text(json.dumps(build(), separators=(",", ":")) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
