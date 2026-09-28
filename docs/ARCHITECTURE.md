# AERODYNE architecture

## Principles → mechanisms

| Principle | Mechanism in code |
|---|---|
| Measure before assuming | Components take `mass_override` / `cg_override`; a measured value always wins and is tagged `MEASURED`. |
| Model before building / simulate before flight | `DigitalTwin.simulation_config()` feeds the 6-DOF simulator and Monte Carlo from the same twin. |
| Test before deployment | SIL runs the compiled C flight code against 14 fault scenarios; `run_preflight` gates arming. |
| Record everything, never destroy raw data | `FlightDataLogger` separates raw / events / estimates with a SHA-256 chain; DB triggers forbid deleting measured datasets. |
| Validate models against real data | `FlightReconstructionEngine` → `comparison.compare` → `model_error.diagnose` → `DigitalTwin.validate_against_flight`. |
| Version everything | `VehicleRegistry` revisions, config hashes, firmware identity, `FlightConfiguration` per flight. |
| Fail safely | Sensor health (OK/DEGRADED/FAILED) gates every use of sensor data; FSM needs sustained, multi-source evidence; reset resumes from NV state. |
| Label every number | `DataKind` on every result; `DataQuality` on every motor/aero dataset; perturbed data becomes `HYPOTHETICAL`. |

## Subsystem interfaces

Each subsystem exposes a small, typed interface; subsystems depend on
interfaces, not on each other's internals.

| Interface | Contract |
|---|---|
| `AtmosphereModel.at(alt_msl) -> AtmosphereState` | T, p, ρ, a, μ and the `DataKind` of the profile. |
| `WindModel.at(alt_agl, t) -> ndarray[3]` | ENU air velocity, m/s. |
| `MotorPerformance` | `thrust_at(t)`, `mass_at(t)`, `total_impulse`, metadata with `source`, `source_date`, `data_quality`. |
| `AeroModel.coefficients(FlightCondition) -> AeroCoefficients` | `cd`, `ca`, `cn_alpha`, `xcp`, `cl`, `cm`, `cn`, fin data for damping, `kind`, `source`. |
| `MassPropertiesEngine` | `configuration(name, motor)`, `at_time(motor, t)`, `cg_travel`. |
| `FlightSimulator(SimulationConfig).run() -> SimulationResult` | Time series + events + `summary()`; `kind = SIMULATED`. |
| `MonteCarloEngine.run(n, seed) -> MonteCarloResult` | Per-run samples/outputs, statistics, landing ellipse, failures recorded (never hidden). |
| `FlightSoftware.step(t, readings) -> TelemetryPacket?` | One flight-software cycle (validate → log → navigate → FSM → health → telemetry). |
| `FlightReconstructionEngine(FlightDataset).run()` | Smoothed altitude/velocity with σ, phases, GNSS track; `kind = DERIVED` (or `HYPOTHETICAL`). |

## Data flow

```text
Vehicle ─┐                                ┌─> MonteCarloEngine ─> dispersion report
Motor ───┼─> DigitalTwin ─> SimulationConfig ─> FlightSimulator ─> SimulationResult ─┐
Aero ────┤                                                                        │
Recovery ┘                                           VirtualSensors (+ faults) <──┘
                                                          │
                                   C / Python FlightSoftware (SIL)  ── TELEMETRY-2 ──> GroundStation
                                                          │
                                         FlightDataLogger (raw, events, estimates)
                                                          │
                          ingestion → FlightReconstructionEngine → compare / diagnose
                                                          │
                              DigitalTwin.validate_against_flight / propose_* → next revision
```

## Ground segment

```text
radio ──bytes──> aerodyne ground (server) ──SSE (raw bytes, base64)──> browser
                     │ append-only capture + SHA-256 manifest          │ TELEMETRY-2 decode (TypeScript)
                     └ sources: UDP | serial | SIL replay | capture     └ GroundStation state, plots, track
```

The server never decodes or filters: the browser sees corruption, duplicates
and gaps exactly as received. Python (`aerodyne.ground.station`) and TypeScript
(`ground-ui/src/station.ts`) implement the same receiver and landing-estimate
algorithm; a shared fixture generated from a SIL run with corrupted packets
(`python -m aerodyne.ground.fixtures`) is asserted by both test suites.

Landing estimate: least-squares drift over the last 5 s of settled descent,
time-to-ground from the descent plan (main deploy altitude and rate from the
digital twin's recovery configuration) or, without a plan, bracketed between
the current rate and a slow main. The radius combines GNSS noise propagated
through the fit, descent-time uncertainty and a wind-shear allowance; in SIL
the true landing point lies inside it throughout the descent. No estimate is
shown when the altitude is not baro-aided.

## Model fidelity and known limitations

* **Aerodynamics (analytical):** Barrowman normal force/CP, small-angle; drag
  build-up is an empirical ESTIMATE, weakest in the transonic region. Import
  RASAero/CFD tables for high-Mach work and compare with `compare_models`.
* **6-DOF:** flat, non-rotating Earth; no jet damping, thrust misalignment,
  fin cant/roll dynamics or rail tip-off. Descent under recovery is 3-DOF.
* **Propellant mass** is assumed consumed proportionally to delivered impulse.
* **Monte Carlo** default uncertainties are placeholders — set them from your
  own measurements (scale weighings, motor lot data, wind soundings).
* **Opening-shock loads** use an assumed load factor Cx; verify against the
  parachute manufacturer's data.

## Master prompt coverage

Sections 1–48 of the master development prompt are covered as listed in the
README table. Section 49 (digital-twin validation) is implemented as
`DigitalTwin.validate_against_flight` / `propose_drag_calibration`; the prompt
text received was truncated at that point, so any requirements after it are
not yet reflected here.
