"""ThrustCurve.org search/download (network mocked) and import with provenance."""

import base64
import json
import urllib.error

import pytest

from aerodyne.app import services as svc
from aerodyne.core.provenance import DataQuality
from aerodyne.propulsion import thrustcurve
from aerodyne.workspace import Workspace

ENG = """; test curve
H128W 29 194 6-10-14 0.094 0.206 AT
0.02 150.0
0.5 140.0
1.2 120.0
1.6 0.0
"""


class _Reply:
    def __init__(self, obj):
        self.body = json.dumps(obj).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def _opener(log):
    def op(req, timeout):
        body = json.loads(req.data.decode())
        log.append((req.full_url, body))
        if req.full_url.endswith("search.json"):
            return _Reply({"results": [{"motorId": "5f4294d20002e900000002a1", "manufacturer": "AeroTech",
                                        "designation": "H128W", "commonName": "H128", "impulseClass": "H", "diameter": 29,
                                        "totImpulseNs": 219.0, "avgThrustN": 128.0, "burnTimeS": 1.7, "extra": "ignored"}]})
        return _Reply({"results": [
            {"simfileId": "u1", "source": "user", "format": "RASP", "data": base64.b64encode(ENG.encode()).decode()},
            {"simfileId": "c1", "source": "cert", "format": "RASP", "data": base64.b64encode(ENG.encode()).decode(),
             "infoUrl": "https://www.thrustcurve.org/simfiles/c1/"}]})
    return op


def test_search_and_download_prefer_certified():
    log = []
    res = thrustcurve.search({"commonName": "H128", "diameter": "29", "manufacturer": ""}, opener=_opener(log))
    assert res[0]["designation"] == "H128W" and "extra" not in res[0]
    assert log[0][1] == {"commonName": "H128", "diameter": 29.0, "maxResults": 40}
    f = thrustcurve.download("5f4294d20002e900000002a1", opener=_opener(log))
    assert f["source_kind"] == "cert" and f["quality"] == DataQuality.CERTIFIED and f["simfile_id"] == "c1"
    assert log[1][1] == {"motorIds": ["5f4294d20002e900000002a1"], "format": "RASP", "data": "file"}
    with pytest.raises(ValueError):
        thrustcurve.search({})
    with pytest.raises(ValueError):
        thrustcurve.download("../etc/passwd")


def test_offline_and_import_with_provenance(tmp_path):
    def offline(req, timeout):
        raise urllib.error.URLError("no route")
    with pytest.raises(thrustcurve.ThrustCurveError, match="not reachable"):
        thrustcurve.search({"impulseClass": "H"}, opener=offline)
    with pytest.raises(svc.BadRequest):
        svc.thrustcurve_search({"impulseClass": "H"}, fetch=lambda c: thrustcurve.search(c, opener=offline))

    ws = Workspace.init(tmp_path / "ws", "t")
    log = []
    out = svc.thrustcurve_import(ws, "abc123", fetch=lambda mid: thrustcurve.download(mid, opener=_opener(log)))
    assert out["data_quality"] == "CERTIFIED" and "ThrustCurve.org simfile c1 (certification data)" in out["source"]
    m = ws.motor(out["keys"][0])
    assert m.metadata.designation == "H128W" and m.metadata.data_quality == DataQuality.CERTIFIED
    assert m.total_impulse == pytest.approx(0.5 * (0.02 * 150) + 0.5 * (150 + 140) * 0.48 + 0.5 * (140 + 120) * 0.7
                                            + 0.5 * 120 * 0.4, rel=0.02)
