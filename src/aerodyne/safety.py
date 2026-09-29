"""High-power rocketry safety-code and certification checks for launch day.

Encodes the quantitative items of the NAR High Power Rocketry Safety Code (Tripoli's code
is equivalent on these points), NAR/TRA certification levels and the FAA Part 101.25 weather
limits for Class 2 rockets. It is a checklist aid: your club's code, the range's rules, the
waiver and the RSO always take precedence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

FT = 0.3048
MPH = 0.44704

# NAR/TRA certification: highest total impulse a flyer may use at each level (N·s)
CERT_LIMITS = [(0, 160.0, "model rocket (no HPR certification)"), (1, 640.0, "Level 1 (H, I)"),
               (2, 5120.0, "Level 2 (J, K, L)"), (3, 40960.0, "Level 3 (M, N, O)")]

# NAR HPR Safety Code minimum distance table: installed total impulse (N·s) ->
# (cleared-area diameter, personnel distance, personnel distance for complex rockets), feet
DISTANCE_TABLE = [
    (320.0, "H or smaller", 50, 100, 200), (640.0, "I", 50, 100, 200), (1280.0, "J", 50, 100, 200),
    (2560.0, "K", 75, 200, 300), (5120.0, "L", 100, 300, 500), (10240.0, "M", 125, 500, 1000),
    (20480.0, "N", 125, 1000, 1500), (40960.0, "O", 125, 1500, 2000),
]

MAX_WIND_MPS = 20 * MPH            # "I will not launch ... in winds above 20 miles per hour"
MAX_LAUNCH_ANGLE_DEG = 20.0        # launcher within 20 degrees of vertical
MIN_THRUST_TO_WEIGHT = 3.0         # liftoff weight <= 1/3 of the motor's average thrust
FAA_MAX_CLOUD_COVER_PCT = 50.0     # 14 CFR 101.25(b): not into more than five-tenths cloud cover
FAA_MIN_VISIBILITY_M = 5 * 1609.344  # 101.25(c): horizontal visibility at least five miles
LANDING_ENERGY_GUIDE_J = 75 * 1.3558   # widely used guideline: 75 ft·lbf per tethered section


@dataclass(frozen=True)
class SafetyItem:
    id: str
    name: str
    status: str          # PASS | FAIL | WARN | INFO | NOT SET
    value: str
    requirement: str
    source: str


def required_cert_level(total_impulse_ns: float) -> tuple[int, str]:
    for level, limit, label in CERT_LIMITS:
        if total_impulse_ns <= limit:
            return level, label
    return 4, "beyond Level 3 (over 40,960 N·s: research / special waiver)"


def minimum_distances(total_impulse_ns: float) -> dict[str, Any]:
    for limit, cls, clear_ft, pers_ft, complex_ft in DISTANCE_TABLE:
        if total_impulse_ns <= limit:
            return {"class": cls, "cleared_diameter_m": clear_ft * FT, "personnel_m": pers_ft * FT,
                    "personnel_complex_m": complex_ft * FT, "cleared_diameter_ft": clear_ft,
                    "personnel_ft": pers_ft, "personnel_complex_ft": complex_ft}
    return {"class": "over O", "cleared_diameter_m": None, "personnel_m": None, "personnel_complex_m": None}


def safety_review(*, total_impulse_ns: float, average_thrust_n: float, liftoff_mass_kg: float,
                  rail_elevation_deg: float, flyer_cert_level: int | None = None,
                  wind_mps: float | None = None, gust_mps: float | None = None,
                  cloud_cover_pct: float | None = None, visibility_m: float | None = None,
                  apogee_agl_m: float | None = None, ceiling_agl_m: float | None = None,
                  landing_energy_j: float | None = None, complex_rocket: bool = False,
                  flutter_ratio: float | None = None, margin_cal: float | None = None, min_margin_cal: float = 1.0,
                  motor_data_quality: str | None = None) -> dict[str, Any]:
    items: list[SafetyItem] = []
    add = items.append
    level, label = required_cert_level(total_impulse_ns)
    if flyer_cert_level is None:
        add(SafetyItem("cert", "Flyer certification", "NOT SET", f"motor needs {label}",
                       "set your certification level in the flyer profile", "NAR/TRA certification levels"))
    else:
        add(SafetyItem("cert", "Flyer certification", "PASS" if flyer_cert_level >= level else "FAIL",
                       f"flyer Level {flyer_cert_level}; motor needs {label}", f"≥ Level {level}",
                       "NAR/TRA certification levels"))
    if motor_data_quality is not None:
        q = motor_data_quality.upper()
        st = "PASS" if q in ("CERTIFIED", "MANUFACTURER", "MEASURED") else "FAIL" if q == "HYPOTHETICAL" else "WARN"
        add(SafetyItem("motor", "Certified commercial motor", st, f"thrust data {q}",
                       "fly only certified, commercially made motors (curve from the cert body or maker)",
                       "NAR HPR Safety Code / Tripoli"))
    if margin_cal is not None:
        add(SafetyItem("stability", "Static stability (loaded)", "PASS" if margin_cal >= min_margin_cal else "FAIL",
                       f"{margin_cal:.2f} cal", f"≥ {min_margin_cal:.1f} cal (mission limit; codes require a stable rocket)",
                       "NAR HPR Safety Code: launch only stable rockets"))
    tw = average_thrust_n / (liftoff_mass_kg * 9.80665) if liftoff_mass_kg > 0 else 0.0
    add(SafetyItem("liftoff_weight", "Liftoff weight vs average thrust", "PASS" if tw >= MIN_THRUST_TO_WEIGHT else "FAIL",
                   f"{tw:.1f} : 1 ({liftoff_mass_kg:.2f} kg, {average_thrust_n:.0f} N average)",
                   "liftoff weight ≤ ⅓ of average thrust (≥ 3 : 1)", "NAR HPR Safety Code"))
    off_vertical = 90.0 - rail_elevation_deg
    add(SafetyItem("launch_angle", "Launcher angle", "PASS" if off_vertical <= MAX_LAUNCH_ANGLE_DEG else "FAIL",
                   f"{off_vertical:.0f}° from vertical", f"≤ {MAX_LAUNCH_ANGLE_DEG:.0f}° from vertical", "NAR HPR Safety Code"))
    if wind_mps is None:
        add(SafetyItem("wind", "Surface wind", "NOT SET", "—", "≤ 20 mph (8.9 m/s)", "NAR HPR Safety Code"))
    else:
        worst = max(wind_mps, gust_mps or 0.0)
        add(SafetyItem("wind", "Surface wind", "PASS" if worst <= MAX_WIND_MPS else "FAIL",
                       f"{wind_mps:.1f} m/s ({wind_mps / MPH:.0f} mph)" + (f", gusts {gust_mps:.1f} m/s" if gust_mps else ""),
                       "≤ 20 mph (8.9 m/s), gusts included", "NAR HPR Safety Code"))
    if cloud_cover_pct is None:
        add(SafetyItem("clouds", "Cloud cover", "NOT SET", "—", "≤ 5/10 (50 %) coverage", "FAA 14 CFR 101.25(b)"))
    else:
        add(SafetyItem("clouds", "Cloud cover", "PASS" if cloud_cover_pct <= FAA_MAX_CLOUD_COVER_PCT else "FAIL",
                       f"{cloud_cover_pct:.0f} %", "≤ 5/10 (50 %) coverage", "FAA 14 CFR 101.25(b)"))
    if visibility_m is None:
        add(SafetyItem("visibility", "Horizontal visibility", "NOT SET", "—", "≥ 5 miles (8 km)", "FAA 14 CFR 101.25(c)"))
    else:
        add(SafetyItem("visibility", "Horizontal visibility", "PASS" if visibility_m >= FAA_MIN_VISIBILITY_M else "FAIL",
                       f"{visibility_m / 1000:.1f} km", "≥ 5 miles (8 km)", "FAA 14 CFR 101.25(c)"))
    if apogee_agl_m is not None:
        if ceiling_agl_m:
            add(SafetyItem("ceiling", "Apogee under waiver ceiling", "PASS" if apogee_agl_m <= ceiling_agl_m else "FAIL",
                           f"{apogee_agl_m:.0f} m ({apogee_agl_m / FT:.0f} ft) AGL",
                           f"≤ {ceiling_agl_m:.0f} m ({ceiling_agl_m / FT:.0f} ft) AGL", "FAA waiver / range"))
        else:
            add(SafetyItem("ceiling", "Apogee under waiver ceiling", "NOT SET", f"{apogee_agl_m:.0f} m AGL",
                           "set the waiver ceiling in the mission limits", "FAA waiver / range"))
    if landing_energy_j is not None:
        add(SafetyItem("landing_energy", "Landing kinetic energy", "PASS" if landing_energy_j <= LANDING_ENERGY_GUIDE_J else "WARN",
                       f"{landing_energy_j:.0f} J ({landing_energy_j / 1.3558:.0f} ft·lbf), whole vehicle",
                       "≤ 75 ft·lbf (102 J) per section, a widely used guideline", "recovery guideline"))
    if flutter_ratio is not None:
        add(SafetyItem("flutter", "Fin flutter margin", "PASS" if flutter_ratio >= 1.5 else "WARN" if flutter_ratio >= 1.0 else "FAIL",
                       f"flutter speed {flutter_ratio:.2f}× the flight speed at the worst point", "≥ 1.5× (ESTIMATED method)",
                       "NACA TN 4197"))
    dist = minimum_distances(total_impulse_ns)
    pers = dist["personnel_complex_m" if complex_rocket else "personnel_m"]
    add(SafetyItem("distance", "Minimum personnel distance", "INFO",
                   f"{pers:.0f} m ({dist['personnel_complex_ft' if complex_rocket else 'personnel_ft']} ft)" if pers else "—",
                   f"{dist['class']} class installed impulse" + (", complex rocket" if complex_rocket else ""),
                   "NAR HPR Safety Code distance table"))
    blocking = [i for i in items if i.status == "FAIL"]
    unset = [i for i in items if i.status == "NOT SET"]
    return {"status": "NO-GO" if blocking else ("CHECK" if unset else "GO"),
            "items": [asdict(i) for i in items], "required_cert": {"level": level, "label": label},
            "distances": dist,
            "note": "Checklist aid. Your club's safety code, the range rules, the FAA waiver and the RSO take precedence."}
