"""Tabulated aerodynamic data (CFD, RASAero II, OpenRocket exports, wind tunnel)
and independent-model comparison.

AERODYNE never assumes another tool is correct: :func:`compare_models` reports
the spread between models instead of choosing the most favourable one.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from aerodyne.aero.model import AeroCoefficients, AeroModel, FlightCondition
from aerodyne.core.provenance import DataKind


@dataclass
class TableAeroModel:
    """Coefficients vs Mach. Columns missing from the table (e.g. RASAero gives
    Cd but you may want your own CP) fall back to ``fallback`` - and the result
    is then labelled with the *weaker* of the two provenances."""

    mach: np.ndarray
    cd: np.ndarray
    reference_area: float
    reference_diameter: float
    source: str
    kind: DataKind = DataKind.SIMULATED
    cd_power_on: np.ndarray | None = None
    cn_alpha: np.ndarray | None = None
    xcp: np.ndarray | None = None
    fallback: AeroModel | None = None

    def __post_init__(self) -> None:
        order = np.argsort(self.mach)
        for name in ("mach", "cd", "cd_power_on", "cn_alpha", "xcp"):
            arr = getattr(self, name)
            if arr is not None:
                setattr(self, name, np.asarray(arr, dtype=float)[order])
        if (self.cn_alpha is None or self.xcp is None) and self.fallback is None:
            raise ValueError("table lacks CNa/Xcp; supply a fallback model for stability data")

    @classmethod
    def from_csv(cls, path: str | Path, reference_diameter: float, source: str,
                 kind: DataKind = DataKind.SIMULATED, fallback: AeroModel | None = None,
                 columns: dict[str, str] | None = None) -> "TableAeroModel":
        """CSV with columns mach, cd [, cd_power_on, cn_alpha, xcp]. ``columns``
        maps these names to the file's headers (e.g. RASAero ``{"mach": "Mach",
        "cd": "CD Power-Off", "cd_power_on": "CD Power-On", "cn_alpha": "CNalpha (0 to 4 deg) (per rad)",
        "xcp": "CP"}``). xcp is expected in metres from the nose tip."""
        cols = {"mach": "mach", "cd": "cd", "cd_power_on": "cd_power_on",
                "cn_alpha": "cn_alpha", "xcp": "xcp", **(columns or {})}
        with open(path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            raise ValueError("empty aero table")

        def col(key: str) -> np.ndarray | None:
            h = cols[key]
            if h not in rows[0]:
                return None
            return np.array([float(r[h]) for r in rows])

        import math

        mach = col("mach")
        cd = col("cd")
        if mach is None or cd is None:
            raise ValueError("aero table needs at least mach and cd columns")
        # collapse duplicate Mach rows (e.g. multiple alphas) by keeping alpha~0 first entries
        _, first = np.unique(mach, return_index=True)
        pick = lambda a: None if a is None else a[first]
        return cls(mach=mach[first], cd=cd[first], cd_power_on=pick(col("cd_power_on")),
                   cn_alpha=pick(col("cn_alpha")), xcp=pick(col("xcp")),
                   reference_area=math.pi * reference_diameter ** 2 / 4,
                   reference_diameter=reference_diameter, source=source, kind=kind,
                   fallback=fallback)

    def coefficients(self, cond: FlightCondition) -> AeroCoefficients:
        m = cond.mach
        table = self.cd_power_on if (cond.thrusting and self.cd_power_on is not None) else self.cd
        cd0 = float(np.interp(m, self.mach, table))
        kind = self.kind
        fb = None
        if self.cn_alpha is not None:
            cna = float(np.interp(m, self.mach, self.cn_alpha))
        else:
            fb = self.fallback.coefficients(cond)
            cna = fb.cn_alpha
        if self.xcp is not None:
            xcp = float(np.interp(m, self.mach, self.xcp))
        else:
            fb = fb or self.fallback.coefficients(cond)
            xcp = fb.xcp
        if fb is not None and fb.kind == DataKind.ESTIMATED:
            kind = DataKind.ESTIMATED
        fin_x = fb.fin_station if fb else (self.fallback.coefficients(cond).fin_station
                                           if self.fallback else None)
        fin_cna = fb.fin_cn_alpha if fb else (self.fallback.coefficients(cond).fin_cn_alpha
                                              if self.fallback else 0.0)
        import math

        a = cond.alpha
        cn = cna * math.sin(a)
        return AeroCoefficients(cd=cd0 * math.cos(a) + cn * math.sin(a), ca=cd0, cn_alpha=cna,
                                xcp=xcp, cn=cn, cl=cn * math.cos(a) - cd0 * math.sin(a),
                                cm=-cn * xcp / self.reference_diameter, fin_station=fin_x,
                                fin_cn_alpha=fin_cna, kind=kind, source=self.source)


def compare_models(models: dict[str, AeroModel], machs: Sequence[float],
                   reynolds_per_m: float = 5e6) -> dict[str, object]:
    """Cross-model comparison table: Cd, CNa, Xcp per Mach, plus the spread
    (max - min) and relative spread of Cd. No model is treated as truth."""
    rows = []
    for m in machs:
        cond = FlightCondition(mach=m, reynolds_per_m=reynolds_per_m)
        vals = {name: mdl.coefficients(cond) for name, mdl in models.items()}
        cds = [v.cd for v in vals.values()]
        xcps = [v.xcp for v in vals.values()]
        rows.append({
            "mach": m,
            **{f"cd[{n}]": round(v.cd, 4) for n, v in vals.items()},
            **{f"xcp[{n}]": round(v.xcp, 4) for n, v in vals.items()},
            "cd_spread": round(max(cds) - min(cds), 4),
            "cd_spread_rel": round((max(cds) - min(cds)) / np.mean(cds), 4),
            "xcp_spread_m": round(max(xcps) - min(xcps), 4),
        })
    worst = max(rows, key=lambda r: r["cd_spread_rel"])
    return {"rows": rows, "max_cd_spread_rel": worst["cd_spread_rel"],
            "max_cd_spread_mach": worst["mach"],
            "note": "Differences are reported, not resolved. Investigate large spreads."}
