"""ThrustCurve.org motor search and thrust-curve download (public API v1, no key needed).

Search results are motor metadata; a download returns RASP (.eng) simulator files. Each file
keeps ThrustCurve's own provenance: "cert" files come from the certifying organisation,
"mfr" from the manufacturer, "user" files are contributed by users. That maps onto AERODYNE's
data quality, so a user-contributed curve can never pass readiness as certified data.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any, Callable

from aerodyne.core.provenance import DataQuality

API = "https://www.thrustcurve.org/api/v1"
SEARCH_KEYS = ("commonName", "designation", "manufacturer", "impulseClass", "diameter", "type", "certOrg", "availability")
QUALITY = {"cert": DataQuality.CERTIFIED, "mfr": DataQuality.MANUFACTURER, "user": DataQuality.UNKNOWN}
PREFERENCE = {"cert": 0, "mfr": 1, "user": 2}


class ThrustCurveError(RuntimeError):
    pass


def _post(path: str, body: dict, opener: Callable[..., Any], timeout: float) -> dict:
    req = urllib.request.Request(f"{API}/{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with opener(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise ThrustCurveError(f"ThrustCurve.org not reachable ({getattr(exc, 'reason', exc)}); "
                               "import a downloaded .eng file instead") from exc
    except json.JSONDecodeError as exc:
        raise ThrustCurveError("ThrustCurve.org sent an unreadable reply") from exc


def search(criteria: dict, opener: Callable[..., Any] = urllib.request.urlopen, timeout: float = 15.0,
           max_results: int = 40) -> list[dict]:
    body: dict[str, Any] = {k: criteria[k] for k in SEARCH_KEYS if criteria.get(k) not in (None, "")}
    if not body:
        raise ValueError("give at least one search criterion (name, class, diameter or manufacturer)")
    if "diameter" in body:
        body["diameter"] = float(body["diameter"])
    body["maxResults"] = max(1, min(int(max_results), 100))
    out = _post("search.json", body, opener, timeout)
    if out.get("error"):
        raise ThrustCurveError(f"ThrustCurve.org: {out['error']}")
    keep = ("motorId", "manufacturer", "manufacturerAbbrev", "designation", "commonName", "impulseClass", "diameter",
            "length", "type", "certOrg", "avgThrustN", "maxThrustN", "totImpulseNs", "burnTimeS", "dataFiles",
            "availability", "totalWeightG", "propWeightG", "delays")
    return [{k: r.get(k) for k in keep} for r in out.get("results", [])]


def download(motor_id: str, opener: Callable[..., Any] = urllib.request.urlopen, timeout: float = 15.0) -> dict:
    """Best available RASP file for one motor: certification data first, then manufacturer, then user."""
    if not motor_id or not str(motor_id).replace("-", "").isalnum():
        raise ValueError("invalid ThrustCurve motor id")
    out = _post("download.json", {"motorIds": [motor_id], "format": "RASP", "data": "file"}, opener, timeout)
    files = [f for f in out.get("results", []) if f.get("data")]
    if not files:
        raise ThrustCurveError("ThrustCurve.org has no RASP (.eng) file for this motor")
    best = min(files, key=lambda f: PREFERENCE.get(f.get("source", "user"), 3))
    text = base64.b64decode(best["data"]).decode("utf-8", errors="replace")
    src = best.get("source", "user")
    return {"text": text, "quality": QUALITY.get(src, DataQuality.UNKNOWN), "source_kind": src,
            "simfile_id": best.get("simfileId"), "info_url": best.get("infoUrl"), "license": best.get("license"),
            "files_available": len(files)}
