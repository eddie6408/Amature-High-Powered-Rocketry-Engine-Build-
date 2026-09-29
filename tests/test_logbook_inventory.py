"""Motor inventory (stock, use on a flight) and the flight logbook (outcomes, totals)."""

import pytest

from aerodyne.app.server import App
from aerodyne.workspace import Workspace


def _call(app, m, p, b=None, expect=200):
    code, out = app.dispatch(m, p, {}, b or {})
    assert code == expect, out
    return out


def _flight(app, fid):
    ws = app.ws
    return _call(app, "POST", "/api/flights", {"flight_id": fid, "vehicle_id": "AERODYNE-001", "revision": "REV-A",
                                               "motor_key": ws.motor_keys()[0], "date": "2026-09-20"})


def test_inventory_stock_and_use(tmp_path):
    app = App(Workspace.init(tmp_path / "ws", "t"))
    key = app.ws.motor_keys()[0]
    it = _call(app, "POST", "/api/inventory", {"kind": "reload", "designation": "H200", "motor_key": key, "quantity": 3,
                                               "cost_each": "42.5", "delays": "6-10-14", "location": "cabinet A"})
    case = _call(app, "POST", "/api/inventory", {"kind": "casing", "designation": "38/360 case", "quantity": 1, "cost_each": 70})
    inv = _call(app, "GET", "/api/inventory")
    assert inv["totals"]["motors_on_hand"] == 3 and inv["totals"]["casings"] == 1
    assert inv["totals"]["value"] == pytest.approx(3 * 42.5 + 70)
    prop = app.ws.motor(key).propellant_mass
    if prop:
        assert inv["totals"]["propellant_on_hand_kg"] == pytest.approx(3 * prop)
    _flight(app, "F-100")
    used = _call(app, "POST", f"/api/inventory/{it['id']}/use", {"flight_id": "F-100"})
    assert used["quantity"] == 2 and used["used"][0]["flight_id"] == "F-100"
    _call(app, "POST", f"/api/inventory/{it['id']}/use", {"count": 5}, expect=400)          # not enough stock
    _call(app, "POST", f"/api/inventory/{case['id']}/use", {}, expect=400)                  # casings are reusable
    _call(app, "POST", f"/api/inventory/{it['id']}/use", {"flight_id": "NOPE"}, expect=400)
    edited = _call(app, "POST", "/api/inventory", {**used, "quantity": 4})
    assert edited["id"] == it["id"] and edited["quantity"] == 4 and len(edited["used"]) == 1   # usage history kept
    for bad in ({"designation": ""}, {"designation": "x", "kind": "rocket"}, {"designation": "x", "quantity": -1},
                {"designation": "x", "motor_key": "nope"}):
        _call(app, "POST", "/api/inventory", bad, expect=400)
    _call(app, "DELETE", f"/api/inventory/{case['id']}")
    assert len(_call(app, "GET", "/api/inventory")["items"]) == 1


def test_logbook_outcomes_and_totals(tmp_path):
    app = App(Workspace.init(tmp_path / "ws", "t"))
    for fid in ("F-1", "F-2", "F-3"):
        _flight(app, fid)
    _call(app, "PUT", "/api/flights/F-1/log", {"outcome": "success", "recovered": True, "site_name": "Lucerne", "cert_attempt": True})
    _call(app, "PUT", "/api/flights/F-2/log", {"outcome": "failure", "recovered": False, "damage": "main tangled"})
    _call(app, "PUT", "/api/flights/F-3/log", {"outcome": "scrubbed"})
    _call(app, "PUT", "/api/flights/F-1/log", {"outcome": "exploded"}, expect=400)
    lb = _call(app, "GET", "/api/logbook")
    rows = {r["flight_id"]: r for r in lb["flights"]}
    assert rows["F-1"]["site"] == "Lucerne" and rows["F-1"]["site_source"] == "log" and rows["F-1"]["cert_attempt"]
    assert rows["F-2"]["damage"] == "main tangled" and rows["F-2"]["recovered"] is False
    t = lb["totals"]
    assert t["flights"] == 2 and t["scrubbed"] == 1 and t["success_rate"] == pytest.approx(0.5)
    imp = app.ws.motor(app.ws.motor_keys()[0]).total_impulse
    assert t["total_impulse_ns"] == pytest.approx(2 * round(imp, 2)) and t["max_cert_level_flown"] == 1
    assert list(t["by_class"].values()) == [2]
