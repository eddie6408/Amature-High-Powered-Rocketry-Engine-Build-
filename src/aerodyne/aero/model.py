"""AeroModel interface.

Inputs: Mach, Reynolds number, angle of attack, altitude, configuration.
Outputs: coefficients referenced to the vehicle reference area and diameter.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from aerodyne.core.provenance import DataKind


@dataclass(frozen=True)
class FlightCondition:
    mach: float
    reynolds_per_m: float       # Re / characteristic length (V / nu)
    alpha: float = 0.0          # rad, total angle of attack
    altitude: float = 0.0       # m MSL
    thrusting: bool = False
    configuration: str = "FULL"


@dataclass(frozen=True)
class AeroCoefficients:
    cd: float            # total drag coefficient along the relative wind
    cn_alpha: float      # normal-force slope, 1/rad
    xcp: float           # centre-of-pressure station, m from nose tip
    ca: float = -1.0     # axial-force coefficient (body axes); defaults to cd
    cl: float = 0.0      # lift coefficient (wind axes)
    cm: float = 0.0      # pitching-moment coefficient about the nose tip, ref d
    cn: float = 0.0      # normal-force coefficient at the given alpha
    fin_station: float | None = None   # fin aerodynamic centre, for pitch damping
    fin_cn_alpha: float = 0.0
    kind: DataKind = DataKind.ESTIMATED
    source: str = ""

    def __post_init__(self) -> None:
        if self.ca < 0:
            object.__setattr__(self, "ca", self.cd)


class AeroModel(Protocol):
    reference_area: float
    reference_diameter: float

    def coefficients(self, cond: FlightCondition) -> AeroCoefficients: ...


class ScaledAeroModel:
    """Wraps a model with multiplicative uncertainty factors (Monte Carlo)."""

    def __init__(self, base: AeroModel, cd_scale: float = 1.0, cn_alpha_scale: float = 1.0,
                 xcp_shift: float = 0.0) -> None:
        self.base = base
        self.cd_scale = cd_scale
        self.cn_alpha_scale = cn_alpha_scale
        self.xcp_shift = xcp_shift
        self.reference_area = base.reference_area
        self.reference_diameter = base.reference_diameter

    def coefficients(self, cond: FlightCondition) -> AeroCoefficients:
        c = self.base.coefficients(cond)
        return replace(c, cd=c.cd * self.cd_scale, ca=c.ca * self.cd_scale, cn_alpha=c.cn_alpha * self.cn_alpha_scale,
                       cn=c.cn * self.cn_alpha_scale, xcp=c.xcp + self.xcp_shift,
                       kind=DataKind.HYPOTHETICAL if (self.cd_scale, self.cn_alpha_scale,
                                                      self.xcp_shift) != (1.0, 1.0, 0.0) else c.kind)
