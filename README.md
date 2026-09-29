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

## Quick start: the application

```bash
pip install -e ".[dev]"                                              # Python >= 3.10
cmake -S firmware -B firmware/build && cmake --build firmware/build  # C flight software (used by SIL)
(cd ui && npm install && npm run build)                              # web app

aerodyne init ~/rockets/my-project --name "My project"
aerodyne app ~/rockets/my-project                                    # http://127.0.0.1:8765
```

The app walks one rocket from idea to flight and back:

| Step | What you do |
|---|---|
| **1 · Design** | Build the vehicle from components or import OpenRocket. Live side profile, mass, CG/CP and stability. Enter measured masses as you build. Revisions lock once flown. |
| **2 · Motors** | Import certified/manufacturer thrust curves (`.eng`) with declared source and quality. |
| **3 · Simulate** | A mission = revision + motor + site + wind + limits. 6-DOF prediction and Monte Carlo dispersion (apogee percentiles, landing ellipse). The **launch simulator** puts your rocket on the rail, runs a pad checklist and countdown, and animates the flight to touchdown under the wind, temperature, humidity and pressure you set, then compares the results with a calm standard day. **My location** uses the device's location services and loads live weather (including wind aloft) for that spot; manual coordinates work offline. |
| **4 · Test & readiness** | Fly the real C flight software through 14 fault scenarios, then get a GO / NO-GO review with evidence for every check. |
| **5 · Fly** | Ground station: serial/UDP radio (or a rehearsal), pad status, live telemetry, ground track, honest landing estimate. The recording is filed with the flight as write-once raw data. |
| **6 · Analyse** | Import altimeter/flight-computer logs (any CSV, units confirmed by you) or the telemetry capture. Reconstruct, compare with the prediction, see possible contributors, then revise the design. |

See the **[user guide](docs/USER_GUIDE.md)** for the step-by-step, launch-day procedure included.

### Command line and developer tools

```bash
./firmware/build/test_firmware                   # firmware unit tests
pytest                                           # Python test suite
(cd ui && npm test)                              # UI tests
aerodyne demo                                    # closed loop on synthetic data, in the terminal
aerodyne sil --all-faults --backend c-app        # fault suite on the C flight application
aerodyne ground --console --udp 5600             # text-only ground station
```

The example vehicle (`AERODYNE-EX1`) flies a **synthetic, HYPOTHETICAL** motor
curve. It shows how the tools fit together; it is not a flight-ready design.

## What is implemented

| Subsystem | Module | Status |
|---|---|---|
| Provenance, record metadata, hashing | `aerodyne.core` | ✅ |
| Configuration management (AERODYNE-001, REV-A…, flown = immutable) | `aerodyne.config` | ✅ |
| Project workspace (vehicles, revisions, motors, missions, runs, flights; write-once raw data) | `aerodyne.workspace` | ✅ file-based, git-friendly |
| Application server (JSON API, background jobs, ground sessions) + web app | `aerodyne.app`, `ui/` | ✅ design → motors → simulate → test/readiness → fly → analyse |
| Readiness review (GO / NO-GO with evidence; stale results never reused) | `aerodyne.readiness` | ✅ |
| Flight-log import (any CSV: header sniffing, unit mapping, baro-only logs, telemetry captures) | `aerodyne.analysis.flightlog`, `.telemetry_log` | ✅ |
| Engineering database (PostgreSQL, immutability triggers) | `db/schema.sql` | ✅ schema for multi-user deployments; the app uses the file workspace |
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
| Flight-software application cycle in C (validate → attitude → KF → FSM → health → telemetry, NV reset recovery) | `firmware/src/aero_app.c` | ✅ passes all SIL fault scenarios |
| RTOS task table, HAL, firmware versioning | `firmware/include/aero_tasks.h`, `aero_hal.h`, `aero_version.h` | ✅ design + host build; no MCU port yet (no ARM toolchain/board) |
| Software-in-the-loop with 11 fault types | `aerodyne.sil` | ✅ backends: `python`, `c` (C FSM + filter), `c-app` (complete C application) |
| Hardware-in-the-loop | `aerodyne.sil.hil` | ⚠️ protocol + bridge only; needs hardware |
| Ground station core (link stats, GPS quality, landing estimate with honest radius) | `aerodyne.ground` | ✅ |
| Ground station in the app (React + TypeScript, own TELEMETRY-2 decoder) | `ui/` | ✅ pad status, live tiles, plots, ground track, sensor health, table view, light/dark |
| Ground server (SSE relay, UDP/serial/SIL/capture sources, raw capture + SHA-256) | `aerodyne.ground.server` | ✅ |
| Flight data ingestion + time normalization | `aerodyne.analysis.ingestion` | ✅ |
| Post-flight reconstruction (KF + RTS smoother, phases) | `aerodyne.analysis.reconstruction` | ✅ |
| Simulation vs reality, model-error "possible contributors" | `aerodyne.analysis.comparison`, `.model_error` | ✅ |
| Generic test-stand data (raw/filtered/derived/uncertainty/plots) | `aerodyne.analysis.test_data` | ✅ |
| Digital twin + post-flight validation + calibration proposals | `aerodyne.twin` | ✅ |
| Reporting (Markdown) | `aerodyne.reporting` | ✅ |
| CAD integration: STEP (exact B-rep via OpenCASCADE/gmsh), STL, Fusion/SolidWorks/FreeCAD mass-property CSV; upload in the designer | `aerodyne.cad` | ✅ STEP needs `pip install ".[cad]"` |
| OpenRocket `.ork` import + cross-validation against OpenRocket's own stored mass/CG/CP/Cd (`aerodyne validate-ork`) | `aerodyne.interop` | ✅ all 22 example files from the OpenRocket repository import; vs OpenRocket on files with supported parts: CP within +10 % (avg ~6 % aft), dry mass/CG within a few % (weighed values honoured), zero-lift Cd mean ±18 %; pods, tube fins, parallel stages reported as unsupported |
| Digital-twin loop: flight → calibration proposal → adopted as a new revision used by later simulations, with history | `aerodyne.app.services`, Design/Analyse pages | ✅ |

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for interfaces and data flow and
[`docs/FIRMWARE.md`](docs/FIRMWARE.md) for the flight-computer design.

## Repository layout

```text
src/aerodyne/     Python platform (engineering, simulation, analysis, SIL, ground)
firmware/         C11 flight-software core + application, HAL/RTOS interfaces, host unit tests
ui/        AERODYNE GROUND web dashboard (React + TypeScript, Vitest)
db/schema.sql     PostgreSQL engineering database
docs/             architecture and firmware design
tests/            pytest suite (includes C-vs-Python flight-software equivalence tests)
```
