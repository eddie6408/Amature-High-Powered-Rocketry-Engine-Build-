"""Mass Properties Engine.

Computes total mass, CG, moments of inertia (parallel-axis theorem), component
contributions and CG movement for named configurations:

FULL, EMPTY, MOTOR_INSTALLED, MOTOR_SPENT, PAYLOAD_INSTALLED, PAYLOAD_REMOVED,
RECOVERY_DEPLOYED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

from aerodyne.core.provenance import DataKind

if TYPE_CHECKING:  # pragma: no cover
    from aerodyne.propulsion.motor import MotorPerformance
    from aerodyne.vehicle.vehicle import Vehicle


@dataclass(frozen=True)
class Contribution:
    name: str
    mass: float
    cg: float
    kind: DataKind


@dataclass(frozen=True)
class MassProperties:
    mass: float        # kg
    cg: float          # m from nose tip
    ixx: float         # kg m^2 roll, about CG
    iyy: float         # kg m^2 pitch/yaw, about CG
    contributions: tuple[Contribution, ...] = field(default_factory=tuple)

    @property
    def kind(self) -> DataKind:
        """MEASURED only if every contribution is measured."""
        kinds = {c.kind for c in self.contributions}
        return DataKind.MEASURED if kinds == {DataKind.MEASURED} else DataKind.ESTIMATED


def combine(items: Iterable[tuple[str, float, float, float, float, DataKind]]) -> MassProperties:
    """items: (name, mass, cg, ixx_own, iyy_own, kind)."""
    items = [i for i in items if i[1] > 0.0]
    m = sum(i[1] for i in items)
    if m <= 0:
        raise ValueError("configuration has no mass")
    cg = sum(i[1] * i[2] for i in items) / m
    ixx = sum(i[3] for i in items)
    iyy = sum(i[4] + i[1] * (i[2] - cg) ** 2 for i in items)
    contribs = tuple(Contribution(i[0], i[1], i[2], i[5]) for i in items)
    return MassProperties(m, cg, ixx, iyy, contribs)


CONFIGURATIONS = {
    # name: (tags excluded, motor state: "full" | "spent" | None)
    "FULL": ((), "full"),
    "MOTOR_INSTALLED": ((), "full"),
    "MOTOR_SPENT": ((), "spent"),
    "EMPTY": ((), None),
    "PAYLOAD_INSTALLED": ((), "full"),
    "PAYLOAD_REMOVED": (("payload",), "full"),
    "RECOVERY_DEPLOYED": ((), "spent"),
}


class MassPropertiesEngine:
    def __init__(self, vehicle: "Vehicle") -> None:
        self.vehicle = vehicle

    def _items(self, exclude_tags: Iterable[str] = ()):
        ex = set(exclude_tags)
        for c in self.vehicle.components:
            if c is self.vehicle.motor_slot or (c.tags & ex):
                continue
            ixx, iyy = c.inertia()
            yield (c.name, c.mass, c.cg, ixx, iyy, c.mass_kind)

    def dry(self, exclude_tags: Iterable[str] = ()) -> MassProperties:
        return combine(self._items(exclude_tags))

    def at_motor_mass(self, motor_mass: float, exclude_tags: Iterable[str] = ()) -> MassProperties:
        items = list(self._items(exclude_tags))
        slot = self.vehicle.motor_slot
        if slot is not None and motor_mass > 0:
            kx, ky = slot.inertia_per_mass()
            items.append(("motor", motor_mass, slot.cg, motor_mass * kx, motor_mass * ky,
                          DataKind.ESTIMATED))
        return combine(items)

    def configuration(self, name: str, motor: "MotorPerformance | None" = None) -> MassProperties:
        try:
            exclude, state = CONFIGURATIONS[name.upper()]
        except KeyError:
            raise KeyError(f"unknown configuration {name}; known: {sorted(CONFIGURATIONS)}") from None
        if state is None or motor is None:
            return self.dry(exclude)
        mm = motor.total_mass if state == "full" else motor.spent_mass
        return self.at_motor_mass(mm, exclude)

    def at_time(self, motor: "MotorPerformance", t: float) -> MassProperties:
        return self.at_motor_mass(motor.mass_at(t))

    def cg_travel(self, motor: "MotorPerformance", n: int = 50) -> list[tuple[float, float, float]]:
        """[(t, mass, cg)] over the burn - the CG movement during flight."""
        out = []
        for i in range(n + 1):
            t = motor.burn_time * i / n
            mp = self.at_time(motor, t)
            out.append((t, mp.mass, mp.cg))
        return out
