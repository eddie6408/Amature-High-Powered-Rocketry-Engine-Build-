"""Import measured / forecast wind profiles (soundings, forecast tables) from text.

Accepts CSV/TSV/whitespace with a header naming altitude, speed and direction
columns; units are taken from the header ((ft), (kt|knots), (mph), (m/s)) or
from the arguments. Directions are meteorological (wind FROM, degrees true).
"""

from __future__ import annotations

import re

from aerodyne.analysis.flightlog import _parse_rows

_SPEED = {"m/s": 1.0, "kt": 0.514444, "mph": 0.44704, "km/h": 1 / 3.6}
_LEN = {"m": 1.0, "ft": 0.3048}


def parse_wind_profile(text: str, altitude_ref: str = "AGL", site_altitude_msl: float = 0.0) -> dict:
    header, rows = _parse_rows(text)
    low = [h.lower() for h in header]

    def find(pat: str) -> int:
        for i, h in enumerate(low):
            if re.search(pat, h):
                return i
        raise ValueError(f"no column matching /{pat}/ in {header}")

    ia, isp, idr = find(r"alt|height|hght|level"), find(r"speed|spd|sknt|wind\s*s|ws"), find(r"dir|drct|from|wd")
    ha, hs = low[ia], low[isp]
    lu = "ft" if re.search(r"\bft\b|feet|\(ft\)", ha) else "m"
    su = ("kt" if re.search(r"kt|knot|sknt", hs) else "mph" if "mph" in hs else
          "km/h" if re.search(r"km/?h|kph", hs) else "m/s")
    alts, speeds, dirs = [], [], []
    for r in rows:
        try:
            a, s, d = float(r[ia]) * _LEN[lu], float(r[isp]) * _SPEED[su], float(r[idr]) % 360
        except (ValueError, IndexError):
            continue
        if altitude_ref.upper() == "MSL":
            a -= site_altitude_msl
        if a >= -1:
            alts.append(max(a, 0.0)); speeds.append(s); dirs.append(d)
    if len(alts) < 2:
        raise ValueError("need at least two wind levels")
    order = sorted(range(len(alts)), key=lambda i: alts[i])
    return {"model": "layered", "altitudes": [round(alts[i], 1) for i in order],
            "speeds": [round(speeds[i], 2) for i in order], "from_deg": [round(dirs[i], 1) for i in order],
            "units_detected": {"altitude": lu, "speed": su, "reference": altitude_ref.upper()}}
