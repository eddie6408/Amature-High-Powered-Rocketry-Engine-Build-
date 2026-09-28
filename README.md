# AERODYNE

**Integrated high-power rocket engineering, simulation, avionics and flight-test platform.**

AERODYNE is built as a closed engineering loop, not just a simulator:

```text
DESIGN → MODEL → SIMULATE → BUILD → TEST → FLY → MEASURE → ANALYZE → COMPARE AGAINST MODEL → IMPROVE
```

Everything it produces is labelled **MEASURED**, **SIMULATED**, **ESTIMATED**,
**DERIVED** or **HYPOTHETICAL**, so simulated or estimated numbers are never
presented as flight data.

> **Scope and safety.** AERODYNE is for lawful amateur/high-power rocketry.
> It treats propulsion as an *input*: commercially made and certified motors,
> published performance data, and properly measured thrust data. It contains no
> propellant formulation, energetic-material, pressure-vessel or ignition-system
> construction content, and it will not generate any. Fly under your national
> association's safety code (NAR/TRA/UKRA/…) and your local regulations.

## Quick start

```bash
pip install -e ".[dev]"                                   # Python >= 3.10
cmake -S firmware -B firmware/build && cmake --build firmware/build   # C flight software
./firmware/build/test_firmware                            # firmware unit tests
pytest                                                    # full test suite

aerodyne stability              # mass properties + static margin of the example vehicle
aerodyne simulate               # 6-DOF flight simulation
aerodyne montecarlo -n 100      # dispersion analysis (apogee, landing ellipse, ...)
aerodyne sil --all-faults --backend c   # run the compiled C flight software against 14 fault scenarios
aerodyne demo                   # the full closed loop on synthetic flight data
```

The example vehicle (`AERODYNE-EX1`) flies a **synthetic, HYPOTHETICAL** motor
curve. It shows how the tools fit together; it is not a flight-ready design.

## What is implemented

| Subsystem | Module | Status |
|---|---|---|
| Provenance, record metadata, hashing | `aerodyne.core` | ✅ |
| Configuration management (AERODYNE-001, REV-A…, flown = immutable) | `aerodyne.config` | ✅ |
| Engineering database (PostgreSQL, immutability triggers) | `db/schema.sql` | ✅ schema; ORM layer not yet |
| Vehicle designer (parametric components, materials) | `aerodyne.vehicle` | ✅ |
| Mass-properties engine (CG, inertia, configurations, CG travel) | `aerodyne.vehicle.mass` | ✅ |
| Motor database / performance / `.eng` import-export | `aerodyne.propulsion` | ✅ |
| Measured thrust-data analyzer with uncertainty | `aerodyne.propulsion.analyzer` | ✅ |
| Aerodynamics: Barrowman + drag build-up, CFD/RASAero/OpenRocket tables, model comparison | `aerodyne.aero` | ✅ |
| Atmosphere (US1976, measured profiles, humidity) and wind (constant, power-law, layered, gusts) | `aerodyne.environment` | ✅ |
| 6-DOF flight dynamics with rail, time-varying mass, recovery descent | `aerodyne.dynamics` | ✅ |
| Monte Carlo (distributions, landing-dispersion ellipse, parallel runs) | `aerodyne.montecarlo` | ✅ |
| Structural load cases, FEA import, margins of safety | `aerodyne.structures` | ✅ interface |
| Recovery analysis (descent rates, drift, timeline, opening loads) | `aerodyne.recovery` | ✅ |
| Sensor framework + validation (range/rate/timestamp/status/missing/stale/outlier) | `aerodyne.avionics.sensors`, `firmware/src/aero_sensor.c` | ✅ |
| Navigation (altitude Kalman filter, gyro attitude) | `aerodyne.avionics.estimation`, `firmware/src/aero_kalman.c` | ✅ |
| Flight state machine (debounced, multi-evidence, reset-safe) | `aerodyne.avionics.state_machine`, `firmware/src/aero_fsm.c` | ✅ Python + C, cross-checked |
| Telemetry protocol TELEMETRY-2 (CRC, loss/dup/reorder/corruption handling) | `aerodyne.avionics.telemetry`, `firmware/src/aero_telemetry.c` | ✅ byte-identical |
| Flight data recorder (raw vs events vs estimates, SHA-256 chain) | `aerodyne.avionics.logger` | ✅ |
| Firmware identity/integrity, automated preflight (READY / NOT READY) | `aerodyne.avionics.firmware`, `.preflight` | ✅ |
| RTOS task table, HAL, firmware versioning | `firmware/include/aero_tasks.h`, `aero_hal.h`, `aero_version.h` | ✅ design + host build; no MCU port yet |
| Software-in-the-loop with 11 fault types | `aerodyne.sil` | ✅ runs the compiled C code via ctypes |
| Hardware-in-the-loop | `aerodyne.sil.hil` | ⚠️ protocol + bridge only; needs hardware |
| Ground station core (link stats, GPS quality, landing estimate) | `aerodyne.ground` | ✅ core + text view; React UI not yet |
| Flight data ingestion + time normalization | `aerodyne.analysis.ingestion` | ✅ |
| Post-flight reconstruction (KF + RTS smoother, phases) | `aerodyne.analysis.reconstruction` | ✅ |
| Simulation vs reality, model-error "possible contributors" | `aerodyne.analysis.comparison`, `.model_error` | ✅ |
| Generic test-stand data (raw/filtered/derived/uncertainty/plots) | `aerodyne.analysis.test_data` | ✅ |
| Digital twin + post-flight validation + calibration proposals | `aerodyne.twin` | ✅ |
| Reporting (Markdown) | `aerodyne.reporting` | ✅ |
| CAD integration (STEP/STL/Fusion/SolidWorks/FreeCAD) | — | ⏳ planned; components accept measured mass/CG overrides and a `cad_reference` column exists |
| OpenRocket `.ork` import | — | ⏳ planned (`.eng` motors and CSV aero tables already work) |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for interfaces and data flow and
[`docs/FIRMWARE.md`](docs/FIRMWARE.md) for the flight-computer design.

## Repository layout

```text
src/aerodyne/     Python platform (engineering, simulation, analysis, SIL, ground)
firmware/         C11 flight-software core, HAL/RTOS interfaces, host unit tests
db/schema.sql     PostgreSQL engineering database
docs/             architecture and firmware design
tests/            pytest suite (includes C-vs-Python flight-software equivalence tests)
```
