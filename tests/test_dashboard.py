"""Project dashboard: missions with review state, flights against prediction, check tally."""

from aerodyne.app.server import App
from aerodyne.workspace import Workspace


def test_dashboard_states(tmp_path):
    app = App(Workspace.init(tmp_path / "ws", "t"))
    code, d = app.dispatch("GET", "/api/dashboard", {}, {})
    assert code == 200
    m = d["missions"][0]
    assert m["state"] == "NOT REVIEWED" and m["latest"] == {}
    assert d["checks"] == {"PASS": 0, "WARN": 0, "FAIL": 0, "NOT RUN": 0, "INFO": 0}
    assert d["totals"]["mean_abs_apogee_error_pct"] is None

    app.dispatch("POST", "/api/missions/example/simulate", {}, {})
    app.dispatch("POST", "/api/missions/example/readiness", {}, {})
    d = app.dispatch("GET", "/api/dashboard", {}, {})[1]
    m = d["missions"][0]
    assert m["state"] == "NO-GO"                                  # synthetic motor data blocks readiness
    assert m["latest"]["simulation"]["current"] and m["latest"]["simulation"]["summary"]["apogee_agl_m"] > 100
    assert sum(d["checks"].values()) > 5 and d["checks"]["FAIL"] >= 1
    assert [r["kind"] for r in d["recent_runs"][:2]] == ["readiness", "simulation"]

    # editing the mission makes the review stale
    mis = app.dispatch("GET", "/api/missions/example", {}, {})[1]
    mis["limits"]["min_margin_cal"] = 1.2
    app.dispatch("POST", "/api/missions", {}, mis)
    d = app.dispatch("GET", "/api/dashboard", {}, {})[1]
    assert d["missions"][0]["state"] == "STALE" and sum(d["checks"].values()) == 0
