"""Motor data interchange: RASP ``.eng`` (used by OpenRocket, RockSim, ThrustCurve)
and simple two-column CSV thrust data."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from aerodyne.core.provenance import DataQuality
from aerodyne.propulsion.motor import MotorMetadata, MotorPerformance


def read_eng(path: str | Path, data_quality: DataQuality = DataQuality.UNKNOWN,
             source: str | None = None, source_date: str = "unknown") -> list[MotorPerformance]:
    """Parse a RASP .eng file (may contain several motors).

    The file format does not state data quality, so the caller must declare it;
    the default is UNKNOWN rather than an optimistic guess.
    """
    text = Path(path).read_text(errors="replace")
    motors: list[MotorPerformance] = []
    header: list[str] | None = None
    comments: list[str] = []
    pts: list[tuple[float, float]] = []

    def flush() -> None:
        nonlocal header, pts, comments
        if header is None:
            return
        name, dia, length, delays, mprop, mtot, mfr = header[:7]
        meta = MotorMetadata(
            manufacturer=mfr, designation=name, source=source or str(path),
            source_date=source_date, data_quality=data_quality,
            diameter_mm=float(dia), length_mm=float(length), delays=delays,
            notes=" ".join(comments).strip())
        t, f = zip(*pts)
        motors.append(MotorPerformance(np.array(t), np.array(f), total_mass=float(mtot),
                                       propellant_mass=float(mprop) or None, metadata=meta))
        header, pts, comments = None, [], []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(";"):
            if header is None:
                comments.append(line.lstrip("; "))
            continue
        parts = line.split()
        if header is None:
            if len(parts) < 7:
                raise ValueError(f"malformed .eng header: {line!r}")
            header = parts
            continue
        if len(parts) >= 2:
            try:
                t, f = float(parts[0]), float(parts[1])
            except ValueError:
                flush()
                header = parts
                continue
            pts.append((t, f))
            if f == 0.0 and t > 0.0:
                flush()
    flush()
    return motors


def write_eng(motor: MotorPerformance, path: str | Path) -> None:
    m = motor.metadata
    lines = [f"; {m.designation} - {m.manufacturer}",
             f"; source: {m.source} ({m.source_date}), quality: {m.data_quality.value}",
             f"{m.designation.replace(' ', '_')} {m.diameter_mm or 0:.0f} {m.length_mm or 0:.0f} "
             f"{m.delays or 'P'} {motor.propellant_mass or 0:.4f} {motor.total_mass:.4f} "
             f"{m.manufacturer.replace(' ', '_')}"]
    for t, f in zip(motor.time, motor.thrust):
        if t == 0.0 and f == 0.0:
            continue
        lines.append(f"   {t:.4f} {f:.3f}")
    Path(path).write_text("\n".join(lines) + "\n")


def read_thrust_csv(path: str | Path, time_col: str = "time", force_col: str = "force"
                    ) -> tuple[np.ndarray, np.ndarray]:
    """Read raw time/force samples (e.g. from a load-cell logger)."""
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    t = np.array([float(r[time_col]) for r in rows])
    f = np.array([float(r[force_col]) for r in rows])
    return t, f
