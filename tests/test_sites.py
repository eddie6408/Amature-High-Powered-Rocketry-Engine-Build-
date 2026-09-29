"""Launch-site library, applying a site to a mission, landing dispersion for the maps."""

from aerodyne.app import services as svc
from aerodyne.app.server import App
from aerodyne.workspace import Workspace


def _call(app, m, p, b=None, expect=200):
    code, out = app.dispatch(m, p, {}, b or {})
    assert code == expect, out
    return out


def test_site_crud_and_validation(tmp_path):
    app = App(Workspace.init(tmp_path / "ws", "t"))
    a = _call(app, "POST", "/api/sites", {"name": "Lucerne Dry Lake", "latitude": "34.5", "longitude": -116.95,
                                          "altitude_msl": 870, "waiver_ceiling_agl_m": 3657, "field_radius_m": 1200})
    assert a["id"] == "lucerne-dry-lake" and a["latitude"] == 34.5
    b = _call(app, "POST", "/api/sites", {"name": "Lucerne Dry Lake", "latitude": 34.6, "longitude": -116.9})
    assert b["id"] == "lucerne-dry-lake-2"                          # names can repeat, ids can't
    upd = _call(app, "POST", "/api/sites", {**a, "field_radius_m": 1500})
    assert upd["id"] == a["id"] and len(_call(app, "GET", "/api/sites")) == 2
    _call(app, "POST", "/api/sites", {"name": "x", "latitude": 95, "longitude": 0}, expect=400)
    _call(app, "POST", "/api/sites", {"name": "", "latitude": 1, "longitude": 0}, expect=400)
    _call(app, "POST", "/api/sites", {"name": "y", "latitude": 1, "longitude": 0, "field_radius_m": -5}, expect=400)
    _call(app, "DELETE", "/api/sites/lucerne-dry-lake-2")
    _call(app, "DELETE", "/api/sites/nope", expect=404)
    assert [s["id"] for s in _call(app, "GET", "/api/sites")] == ["lucerne-dry-lake"]


def test_apply_site_and_dispersion(tmp_path):
    ws = Workspace.init(tmp_path / "ws", "t")
    app = App(ws)
    before = ws.mission("example").to_dict()
    assert "site_id" not in before                                  # older missions keep their hash
    _call(app, "POST", "/api/sites", {"name": "Field", "latitude": 40.1, "longitude": -105.2, "altitude_msl": 1600,
                                      "waiver_ceiling_agl_m": 1500, "field_radius_m": 800, "rail_length_m": 2.4})
    m = _call(app, "POST", "/api/missions/example/apply-site", {"site_id": "field"})
    assert m["site_id"] == "field" and m["site"]["latitude"] == 40.1 and m["site"]["rail_length"] == 2.4
    assert m["limits"]["altitude_ceiling_agl_m"] == 1500 and m["limits"]["field_radius_m"] == 800
    d = _call(app, "GET", "/api/missions/example/dispersion")
    assert d["points"] == [] and d["run_id"] is None and d["field_radius_m"] == 800
    svc.monte_carlo(ws, "example", 6)
    d = _call(app, "GET", "/api/missions/example/dispersion")
    assert len(d["points"]) == 6 and d["current"] and d["ellipse"]["semi_major_m"] >= d["ellipse"]["semi_minor_m"]
    _call(app, "POST", "/api/missions/example/apply-site", {"site_id": "missing"}, expect=404)
