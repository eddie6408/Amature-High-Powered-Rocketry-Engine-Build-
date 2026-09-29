"""Build log: part status and costs, extra costs, budgets, journal, estimated vs weighed mass."""

import pytest

from aerodyne.app.server import App
from aerodyne.workspace import Workspace


def _call(app, m, p, b=None, expect=200):
    code, out = app.dispatch(m, p, {}, b or {})
    assert code == expect, out
    return out


def test_build_log_roundtrip(tmp_path):
    ws = Workspace.init(tmp_path / "ws", "t")
    app = App(ws)
    b = _call(app, "GET", "/api/vehicles/AERODYNE-001/build")
    names = [p["name"] for p in b["parts"]]
    assert "nose cone" in names and all(p["status"] == "planned" for p in b["parts"])
    assert b["totals"]["cost_total"] == 0 and b["totals"]["weighed"] == 0
    assert b["totals"]["current_dry_kg"] == pytest.approx(b["totals"]["estimated_dry_kg"])

    out = _call(app, "PUT", "/api/vehicles/AERODYNE-001/build", {
        "items": {"nose cone": {"status": "installed", "cost": 45.5, "supplier": "Madcow"},
                  "upper airframe": {"status": "ordered", "cost": "60"}, "old part": {"status": "built", "cost": 5}},
        "extras": [{"name": "epoxy", "category": "consumables", "cost": 28, "paid": True},
                   {"name": "paint", "cost": 15}],
        "budget": {"cost": 300, "currency": "usd", "dry_mass_kg": 1.3}})
    t = out["totals"]
    assert t["cost_parts"] == pytest.approx(45.5 + 60 + 0) and t["cost_extras"] == 43
    assert t["cost_total"] == pytest.approx(148.5) and t["cost_committed"] == pytest.approx(45.5 + 60 + 28)
    assert t["parts_by_status"]["installed"] == 1 and t["parts_by_status"]["ordered"] == 1
    assert out["orphan_items"] == ["old part"] and out["budget"]["dry_mass_kg"] == 1.3
    for bad in ({"items": {"x": {"status": "lost"}}}, {"items": {"x": {"cost": -1}}}, {"budget": {"cost": "lots"}}):
        _call(app, "PUT", "/api/vehicles/AERODYNE-001/build", bad, expect=400)
    _call(app, "GET", "/api/vehicles/NOPE/build", expect=404)

    e1 = _call(app, "POST", "/api/vehicles/AERODYNE-001/build/log", {"title": "Fins tacked", "hours": 2.5, "date": "2026-09-01"})
    _call(app, "POST", "/api/vehicles/AERODYNE-001/build/log", {"title": "Fillets", "hours": "3", "date": "2026-08-30"})
    _call(app, "POST", "/api/vehicles/AERODYNE-001/build/log", {"title": ""}, expect=400)
    b = _call(app, "GET", "/api/vehicles/AERODYNE-001/build")
    assert [e["title"] for e in b["log"]] == ["Fillets", "Fins tacked"] and b["totals"]["hours"] == 5.5
    _call(app, "DELETE", f"/api/vehicles/AERODYNE-001/build/log/{e1['id']}")
    assert _call(app, "GET", "/api/vehicles/AERODYNE-001/build")["totals"]["hours"] == 3


def test_weighed_parts_change_the_mass_budget(tmp_path):
    ws = Workspace.init(tmp_path / "ws", "t")
    app = App(ws)
    d = ws.design("AERODYNE-001")
    nose = next(c for c in d.vehicle.components if c.name == "nose cone")
    est = nose.estimated_mass()
    nose.mass_override = est + 0.05
    ws.save_design("AERODYNE-001", "REV-A", d)
    b = _call(app, "GET", "/api/vehicles/AERODYNE-001/build")
    p = next(x for x in b["parts"] if x["name"] == "nose cone")
    assert p["measured_kg"] == pytest.approx(est + 0.05) and p["delta_kg"] == pytest.approx(0.05)
    assert b["totals"]["weighed"] == 1
    assert b["totals"]["current_dry_kg"] == pytest.approx(b["totals"]["estimated_dry_kg"] + 0.05)
