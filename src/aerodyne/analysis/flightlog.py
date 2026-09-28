"""Import flight logs from any altimeter / flight computer that exports text.

Commercial altimeters (and our own flight computer) export CSV/TSV with
vendor-specific headers and units. Rather than hard-coding vendor formats,
the importer *sniffs* the header and proposes a :class:`ColumnMapping`
(columns + units); the user confirms or corrects it, and the mapping is stored
with the flight so the analysis is reproducible. The raw file is never
modified - conversion produces a separate analysis dataset.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass

import numpy as np

from aerodyne.analysis.ingestion import Channel, FlightDataset
from aerodyne.core.provenance import DataKind
from aerodyne.environment.atmosphere import G0, StandardAtmosphere

FT = 0.3048


@dataclass
class ColumnMapping:
    time: str
    time_unit: str = "s"                    # s | ms | us
    altitude: str | None = None
    altitude_unit: str = "m"                # m | ft
    pressure: str | None = None
    pressure_unit: str = "Pa"               # Pa | hPa | mbar | kPa | inHg
    accel: str | None = None                # axial accelerometer
    accel_unit: str = "mps2"                # mps2 | g | ftps2
    accel_includes_gravity: bool = True     # True: raw accelerometer (reads +1 g on the pad)
    accel_sign: float = 1.0                 # -1 if the sensor axis points aft
    accel_y: str | None = None
    accel_z: str | None = None
    gyro_x: str | None = None
    gyro_y: str | None = None
    gyro_z: str | None = None
    gyro_unit: str = "rad/s"                # rad/s | deg/s
    latitude: str | None = None
    longitude: str | None = None
    gps_altitude: str | None = None
    gps_altitude_unit: str = "m"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ColumnMapping":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


_PATTERNS = [
    ("time", r"^(time|t|timestamp|elapsed|seconds|sec)\b|time\s*\("),
    ("pressure", r"press|baro.*(pa|mbar)|^p\b"),
    ("gps_altitude", r"(gps|gnss).*alt"),
    ("altitude", r"alt|height|agl|msl"),
    ("latitude", r"^lat|latitude"),
    ("longitude", r"^lon|^lng|longitude"),
    ("gyro_x", r"gyro.*x|^gx\b|rate.*x"),
    ("gyro_y", r"gyro.*y|^gy\b|rate.*y"),
    ("gyro_z", r"gyro.*z|^gz\b|rate.*z"),
    ("accel_y", r"acc.*y\b|^ay\b"),
    ("accel_z", r"acc.*z\b|^az\b"),
    ("accel", r"acc(el)?(eration)?(.*x\b)?|^ax\b|^a\b"),
]


def _detect_units(header: str) -> dict[str, str]:
    h = header.lower()
    u: dict[str, str] = {}
    if re.search(r"\(ms\)|\bms\b|millis", h):
        u["time"] = "ms"
    elif re.search(r"\(us\)|micros", h):
        u["time"] = "us"
    if re.search(r"\(ft\)|\bfeet\b|\bft\b|_ft\b", h):
        u["length"] = "ft"
    if re.search(r"\(g\)|\bgs?\b|_g\b|\[g\]", h):
        u["accel"] = "g"
    elif re.search(r"ft/s", h):
        u["accel"] = "ftps2"
    if "hpa" in h or "mbar" in h or "millibar" in h:
        u["pressure"] = "hPa"
    elif "kpa" in h:
        u["pressure"] = "kPa"
    elif "inhg" in h:
        u["pressure"] = "inHg"
    if "deg/s" in h or "dps" in h:
        u["gyro"] = "deg/s"
    return u


def _parse_rows(text: str) -> tuple[list[str], list[list[str]]]:
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", ";", "//"))]
    if not lines:
        raise ValueError("empty log")
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines[:20]), delimiters=",;\t ")
    except csv.Error:
        dialect = csv.excel
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO("\n".join(lines)), dialect)]
    rows = [[c for c in r if c != ""] if dialect.delimiter == " " else r for r in rows]

    def numeric(r: list[str]) -> bool:
        try:
            [float(c) for c in r if c != ""]
            return len(r) > 0
        except ValueError:
            return False

    header_idx = next((i for i, r in enumerate(rows) if not numeric(r)
                       and i + 1 < len(rows) and numeric(rows[i + 1])), None)
    if header_idx is None:
        width = len(rows[0])
        header = [f"col{i}" for i in range(width)]
        data = [r for r in rows if numeric(r)]
    else:
        header = rows[header_idx]
        data = [r for r in rows[header_idx + 1:] if numeric(r)]
    return header, data


def sniff(text: str) -> dict:
    """Return headers, a preview and a *proposed* mapping. Always ask the user
    to confirm it: header conventions vary between vendors and firmware versions."""
    header, data = _parse_rows(text)
    used: set[str] = set()
    guess: dict[str, str | float | bool | None] = {}
    for field, pat in _PATTERNS:
        for h in header:
            if h in used:
                continue
            if re.search(pat, h.strip().lower()):
                guess[field] = h
                used.add(h)
                break
    if "time" not in guess and header:
        guess["time"] = header[0]
    for field, key, unit_field in (("time", "time", "time_unit"), ("altitude", "length", "altitude_unit"),
                                   ("gps_altitude", "length", "gps_altitude_unit"),
                                   ("accel", "accel", "accel_unit"), ("pressure", "pressure", "pressure_unit"),
                                   ("gyro_x", "gyro", "gyro_unit")):
        col = guess.get(field)
        if isinstance(col, str):
            u = _detect_units(col).get(key)
            if u:
                guess[unit_field] = u
    # time in ms is common even without a unit in the header: infer from spacing
    if isinstance(guess.get("time"), str) and "time_unit" not in guess and len(data) > 5:
        ti = header.index(guess["time"])
        tt = np.array([float(r[ti]) for r in data[:200] if len(r) > ti])
        dt = np.median(np.diff(tt)) if len(tt) > 2 else 0.0
        if dt >= 2.0:
            guess["time_unit"] = "ms"
    mapping = ColumnMapping(**{k: v for k, v in guess.items() if k in ColumnMapping.__dataclass_fields__})
    warnings = []
    if mapping.altitude is None and mapping.pressure is None:
        warnings.append("no altitude or pressure column recognised - map one manually")
    if mapping.accel is None:
        warnings.append("no accelerometer column recognised: boost phase will be less accurate")
    return {"columns": header, "rows": len(data), "preview": data[:8], "mapping": mapping.to_dict(),
            "warnings": warnings}


_TIME = {"s": 1.0, "ms": 1e-3, "us": 1e-6}
_LEN = {"m": 1.0, "ft": FT}
_ACC = {"mps2": 1.0, "g": G0, "ftps2": FT}
_PRES = {"Pa": 1.0, "hPa": 100.0, "mbar": 100.0, "kPa": 1000.0, "inHg": 3386.389}
_GYRO = {"rad/s": 1.0, "deg/s": np.pi / 180}


def load_flight_log(text: str, mapping: ColumnMapping, flight_id: str, source: str,
                    kind: DataKind = DataKind.MEASURED) -> FlightDataset:
    header, data = _parse_rows(text)
    idx = {h: i for i, h in enumerate(header)}

    def col(name: str | None) -> np.ndarray | None:
        if name is None:
            return None
        if name not in idx:
            raise ValueError(f"column {name!r} not in log (have {header})")
        i = idx[name]
        return np.array([float(r[i]) if i < len(r) and r[i] != "" else np.nan for r in data])

    t = col(mapping.time) * _TIME[mapping.time_unit]
    order = np.argsort(t, kind="stable")
    keep = np.concatenate([[True], np.diff(t[order]) > 0])      # drop duplicate timestamps
    sel = order[keep]
    t = t[sel]
    ds = FlightDataset(flight_id)
    if len(sel) < len(data):
        ds.notes.append(f"{len(data) - len(sel)} out-of-order/duplicate rows ignored for analysis")

    def clean(v: np.ndarray | None) -> tuple[np.ndarray, np.ndarray] | None:
        if v is None:
            return None
        v = v[sel]
        good = np.isfinite(v)
        return t[good], v[good]

    alt = clean(col(mapping.altitude))
    if alt is not None:
        ds.add(Channel("baro_alt", alt[0], alt[1] * _LEN[mapping.altitude_unit], "m", source, kind))
    elif mapping.pressure is not None:
        p = clean(col(mapping.pressure))
        atm = StandardAtmosphere()
        h = np.array([atm.altitude_from_pressure(x * _PRES[mapping.pressure_unit]) for x in p[1]])
        ds.add(Channel("baro_alt", p[0], h, "m (pressure altitude)", source, kind))
        ds.notes.append("altitude derived from pressure with the standard atmosphere (pressure altitude)")
    else:
        raise ValueError("mapping needs an altitude or a pressure column")

    if mapping.accel is not None:
        k = _ACC[mapping.accel_unit]
        axes = [col(mapping.accel)[sel] * k * mapping.accel_sign]
        for c in (mapping.accel_y, mapping.accel_z):
            axes.append(col(c)[sel] * k if c else np.zeros_like(axes[0]))
        av = np.column_stack(axes)
        if not mapping.accel_includes_gravity:
            av[:, 0] += G0      # kinematic -> specific force (assumes near-vertical flight)
            ds.notes.append("acceleration column treated as kinematic; +1 g added (assumes vertical)")
        good = np.all(np.isfinite(av), axis=1)
        ds.add(Channel("accel", t[good], av[good], "m/s^2", source, kind))
        ds.add(Channel("accel_axial", t[good], av[good, 0], "m/s^2", source, kind))
    if mapping.gyro_x and mapping.gyro_y and mapping.gyro_z:
        gs = [col(c)[sel] * _GYRO[mapping.gyro_unit] for c in (mapping.gyro_x, mapping.gyro_y, mapping.gyro_z)]
        g = np.column_stack(gs)
        good = np.all(np.isfinite(g), axis=1)
        ds.add(Channel("gyro", t[good], g[good], "rad/s", source, kind))
    if mapping.latitude and mapping.longitude:
        lat, lon = col(mapping.latitude)[sel], col(mapping.longitude)[sel]
        galt = col(mapping.gps_altitude)
        galt = galt[sel] * _LEN[mapping.gps_altitude_unit] if galt is not None else np.full_like(lat, np.nan)
        good = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) > 1e-6)
        if good.sum() >= 2:
            ds.add(Channel("gnss", t[good], np.column_stack([lat[good], lon[good], galt[good]]),
                           "deg,deg,m", source, kind))
    return ds
