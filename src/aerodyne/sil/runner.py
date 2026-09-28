"""SIL runner: drives the real flight-software code path with virtual sensors,
injects faults, passes telemetry through a lossy radio model into the ground
receiver, and evaluates the result against simulation truth."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from aerodyne.avionics.fsw import FlightSoftware, FswConfig
from aerodyne.avionics.logger import FlightDataLogger
from aerodyne.avionics.sensors import SensorReading, Status
from aerodyne.avionics.telemetry import TelemetryPacket, TelemetryReceiver
from aerodyne.dynamics.simulator import SimulationResult
from aerodyne.sil.faults import FaultInjection, FaultKind
from aerodyne.sil.virtual_sensors import SensorNoise, VirtualSensors


@dataclass
class RadioModel:
    loss: float = 0.02
    duplicate: float = 0.01
    reorder: float = 0.01
    latency: float = 0.05


@dataclass
class SILResult:
    transitions: list[tuple[float, str, str, str]]     # (t since liftoff, from, to, reason)
    truth_events: list[tuple[float, str]]
    final_state: str
    packets_received: list[TelemetryPacket]
    link: dict[str, float]
    faults_detected: list[str]
    est_apogee: float
    true_apogee: float
    logger: FlightDataLogger
    boots: int
    faults_injected: list[FaultInjection] = field(default_factory=list)

    def transition_time(self, to_state: str) -> float | None:
        return next((t for t, _, s, _ in self.transitions if s == to_state), None)

    def evaluate(self, apogee_early: float = 0.5, apogee_late: float = 4.0,
                 landed_deadline: float = 10.0) -> list[str]:
        """Return a list of problems; empty means the flight software behaved
        predictably for this scenario."""
        issues: list[str] = []
        flight = [s for _, _, s, _ in self.transitions
                  if s in ("ASCENT", "COAST", "DESCENT", "LANDED")]
        if flight != ["ASCENT", "COAST", "DESCENT", "LANDED"]:
            issues.append(f"unexpected flight state sequence {flight}")
        t_lift = self.transition_time("ASCENT")
        truth = dict((n, t) for t, n in self.truth_events)
        if t_lift is None or t_lift < 0 or t_lift > 0.6:
            issues.append(f"liftoff detected at {t_lift}")
        t_apo = self.transition_time("DESCENT")
        true_apo = truth.get("apogee")
        if t_apo is None or true_apo is None:
            issues.append("apogee not detected")
        elif not (true_apo - apogee_early <= t_apo <= true_apo + apogee_late):
            issues.append(f"apogee detected at {t_apo:.2f}s, truth {true_apo:.2f}s")
        t_land = self.transition_time("LANDED")
        true_land = truth.get("landing")
        if t_land is not None and true_land is not None and not (
                true_land - 1.0 <= t_land <= true_land + landed_deadline):
            issues.append(f"landing detected at {t_land:.2f}s, truth {true_land:.2f}s")
        return issues


class SILRunner:
    def __init__(self, sim: SimulationResult, faults: list[FaultInjection] | None = None,
                 backend: str = "python", seed: int = 0, noise: SensorNoise | None = None,
                 radio: RadioModel | None = None, dt: float = 0.01,
                 fsw_config: FswConfig | None = None) -> None:
        self.sim = sim
        self.faults = list(faults or [])
        self.backend = backend
        self.seed = seed
        self.noise = noise
        self.radio = radio or RadioModel()
        self.dt = dt
        self.fsw_config = fsw_config or FswConfig()

    def _apply_sensor_faults(self, t: float, readings: dict[str, SensorReading],
                             stale: dict[str, SensorReading], spiked: set[int]
                             ) -> dict[str, SensorReading]:
        out = dict(readings)
        for i, f in enumerate(self.faults):
            if not f.active(t):
                continue
            if f.kind == FaultKind.IMU_FAILURE:
                for k in ("imu_accel", "imu_gyro"):
                    if k in out:
                        out[k] = SensorReading(k, k, (float("nan"),) * 3, out[k].timestamp,
                                               out[k].sequence, Status.COMM_ERROR)
            elif f.kind == FaultKind.BAROMETER_FAILURE and "baro" in out:
                r = out["baro"]
                out["baro"] = SensorReading("baro", "baro", float("nan"), r.timestamp, r.sequence,
                                            Status.COMM_ERROR)
            elif f.kind == FaultKind.GNSS_LOSS and "gnss" in out:
                r = out["gnss"]
                out["gnss"] = SensorReading("gnss", "gnss", r.value, r.timestamp, r.sequence,
                                            Status.NO_FIX, quality=0.0)
            elif f.kind == FaultKind.SENSOR_SPIKE and f.sensor in out and i not in spiked:
                spiked.add(i)
                r = out[f.sensor]
                if isinstance(r.value, tuple):
                    val = (r.value[0] + f.magnitude, *r.value[1:])
                else:
                    val = r.value + f.magnitude
                out[f.sensor] = SensorReading(r.sensor_id, r.kind, val, r.timestamp, r.sequence,
                                              r.status, r.quality)
            elif f.kind == FaultKind.STALE_DATA and f.sensor in out:
                if f.sensor not in stale:
                    stale[f.sensor] = out[f.sensor]
                out[f.sensor] = stale[f.sensor]      # frozen value and timestamp
            elif f.kind == FaultKind.LOW_BATTERY and "battery" in out:
                r = out["battery"]
                out["battery"] = SensorReading("battery", "battery", f.magnitude, r.timestamp,
                                               r.sequence)
        return out

    def run(self) -> SILResult:
        rng = np.random.default_rng(self.seed + 1)
        vs = VirtualSensors(self.sim, self.noise, seed=self.seed)
        nv: dict = {}
        logger = FlightDataLogger()
        fsw = FlightSoftware(self.fsw_config, logger, nv, backend=self.backend)
        rx = TelemetryReceiver(vehicle_id=self.fsw_config.vehicle_id)
        received: list[TelemetryPacket] = []
        in_flight_radio: list[tuple[float, bytes]] = []
        held: bytes | None = None
        transitions: list[tuple[float, str, str, str]] = []
        stale: dict[str, SensorReading] = {}
        spiked: set[int] = set()
        resets = [f for f in self.faults if f.kind == FaultKind.PROCESSOR_RESET]
        reset_done: set[int] = set()
        boot_blackout_until = -1e9
        faults_seen: set[str] = set()

        t = -vs.pad_time
        steps = int(round((vs.t_end - t) / self.dt))
        for n in range(steps + 1):
            t = -vs.pad_time + n * self.dt
            # clock error: the flight computer's clock jumps by `magnitude`
            clock = t + vs.pad_time + sum(f.magnitude for f in self.faults
                                          if f.kind == FaultKind.CLOCK_ERROR and t >= f.start)
            for idx, f in enumerate(resets):
                if idx not in reset_done and t >= f.start:
                    reset_done.add(idx)
                    faults_seen |= fsw.faults
                    fsw = FlightSoftware(self.fsw_config, logger, nv, backend=self.backend)
                    boot_blackout_until = t + 0.2
            logger.storage_ok = not any(f.kind == FaultKind.STORAGE_FAILURE and f.active(t)
                                        for f in self.faults)
            readings = vs.sample(t)
            if t < boot_blackout_until:
                continue
            readings = {k: SensorReading(r.sensor_id, r.kind, r.value, clock, r.sequence,
                                         r.status, r.quality) for k, r in readings.items()}
            readings = self._apply_sensor_faults(t, readings, stale, spiked)

            if abs(t - (-vs.pad_time + 0.5)) < self.dt / 2:
                fsw.command("preflight")
            elif abs(t - (-vs.pad_time + 1.0)) < self.dt / 2:
                fsw.command("arm", preflight_ok=True)

            before = fsw.fsm.state
            pkt = fsw.step(clock, readings)
            after = fsw.fsm.state
            if after != before:
                transitions.append((round(t, 4), before.name, after.name,
                                    fsw.fsm.transitions[-1].reason))

            if pkt is not None:
                self._transmit(t, pkt, rng, in_flight_radio)
            # deliver radio frames whose latency has elapsed
            due = [x for x in in_flight_radio if x[0] <= t]
            in_flight_radio[:] = [x for x in in_flight_radio if x[0] > t]
            for _, frame in due:
                if self.radio.reorder and rng.random() < self.radio.reorder and held is None:
                    held = frame
                    continue
                for p, _ in rx.feed(frame, t):
                    received.append(p)
                if held is not None:
                    for p, _ in rx.feed(held, t):
                        received.append(p)
                    held = None
        faults_seen |= fsw.faults
        truth_apogee = float(self.sim.position[:, 2].max())
        return SILResult(
            transitions=transitions, truth_events=list(self.sim.events),
            final_state=fsw.fsm.state.name, packets_received=received,
            link={**rx.stats.__dict__, "loss_rate": rx.stats.packet_loss_rate},
            faults_detected=sorted(faults_seen), est_apogee=float(fsw.max_est_alt),
            true_apogee=truth_apogee, logger=logger, boots=int(nv.get("boot_count", 1)),
            faults_injected=self.faults)

    def _transmit(self, t: float, pkt: TelemetryPacket, rng: np.random.Generator,
                  queue: list[tuple[float, bytes]]) -> None:
        if any(f.kind == FaultKind.TELEMETRY_LOSS and f.active(t) for f in self.faults):
            return
        if rng.random() < self.radio.loss:
            return
        frame = bytearray(pkt.encode())
        for f in self.faults:
            if f.kind == FaultKind.CORRUPTED_PACKET and f.active(t) and \
                    rng.random() < f.params.get("probability", 0.5):
                pos = int(rng.integers(2, len(frame)))
                frame[pos] ^= 1 << int(rng.integers(0, 8))
        queue.append((t + self.radio.latency, bytes(frame)))
        if rng.random() < self.radio.duplicate:
            queue.append((t + self.radio.latency + 0.01, bytes(frame)))
