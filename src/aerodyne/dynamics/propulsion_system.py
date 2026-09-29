"""Several motors: clusters, multi-stage stacks, airstarts and side boosters.

A :class:`PropulsionSystem` is a list of :class:`MotorGroup` (identical motors lit together) and
the separation rule of every stage below the top one. Stages are numbered from the top:
0 is the sustainer (or the core of a rocket with side boosters), 1 the stage below it, and so on.
Vehicle components carry their stage in ``Component.stage``.

Ignition of a group: ``launch`` (t = 0), ``time`` (``delay`` s after launch: airstart), or
``separation`` (``delay`` s after the stage directly below it separates).
A stage separates ``delay`` s after all of its motors have burnt out.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from aerodyne.propulsion.motor import MotorPerformance

IGNITIONS = ("launch", "time", "separation")


@dataclass(frozen=True)
class MotorGroup:
    motor: MotorPerformance
    count: int = 1
    stage: int = 0
    ignition: str = "launch"
    delay: float = 0.0
    x: float | None = None            # motor CG station (m from nose tip); None: the stage's motor slot

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError("a motor group needs at least one motor")
        if self.ignition not in IGNITIONS:
            raise ValueError(f"ignition must be one of {', '.join(IGNITIONS)}")
        if self.stage < 0 or self.delay < 0:
            raise ValueError("stage and delay can't be negative")


@dataclass(frozen=True)
class StageSeparation:
    stage: int                        # the stage that separates (>= 1)
    delay: float = 0.5                # s after its motors burn out
    parallel: bool = False            # side boosters (pods), not a stacked stage below


@dataclass(frozen=True)
class PropulsionSystem:
    groups: tuple[MotorGroup, ...]
    separations: tuple[StageSeparation, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.groups:
            raise ValueError("no motors")
        stages = {g.stage for g in self.groups}
        for g in self.groups:
            if g.ignition == "separation" and not any(s.stage == g.stage + 1 for s in self.separations):
                raise ValueError(f"stage {g.stage} lights at separation but stage {g.stage + 1} has no separation rule")
        for s in self.separations:
            if s.stage < 1:
                raise ValueError("stage 0 (the top stage) can't separate")
            if s.stage not in stages:
                raise ValueError(f"stage {s.stage} separates but has no motors")

    # ---- summaries used by safety checks, readiness, cards ------------------------------
    @property
    def total_impulse(self) -> float:
        return sum(g.count * g.motor.total_impulse for g in self.groups)

    @property
    def liftoff_average_thrust(self) -> float:
        """Sum of the average thrust of the motors lit at launch (safety code's 3:1 rule)."""
        return sum(g.count * g.motor.average_thrust for g in self.groups if g.ignition == "launch")

    @property
    def motor_count(self) -> int:
        return sum(g.count for g in self.groups)

    @property
    def is_simple(self) -> bool:
        g = self.groups[0]
        return len(self.groups) == 1 and g.count == 1 and g.stage == 0 and g.ignition == "launch" and not self.separations

    def separation(self, stage: int) -> StageSeparation | None:
        return next((s for s in self.separations if s.stage == stage), None)

    def scaled(self, impulse_scale: float, burn_time_scale: float) -> "PropulsionSystem":
        """Monte Carlo: the same impulse / burn-time dispersion applied to every motor."""
        return replace(self, groups=tuple(replace(g, motor=g.motor.scaled(impulse_scale, burn_time_scale))
                                          for g in self.groups))

    def describe(self) -> list[str]:
        out = []
        for g in self.groups:
            when = {"launch": "at launch", "time": f"{g.delay:g} s after launch",
                    "separation": f"{g.delay:g} s after stage {g.stage + 1} separates"}[g.ignition]
            out.append(f"stage {g.stage}: {g.count} x {g.motor.metadata.designation} {when}")
        for s in self.separations:
            out.append(f"stage {s.stage} {'boosters' if s.parallel else 'booster'} separate {s.delay:g} s after burnout")
        return out
