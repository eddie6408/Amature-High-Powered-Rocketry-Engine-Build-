# AERODYNE user guide: from idea to flight and back

AERODYNE is one application for the whole life of a rocket:

```text
1 Design  →  2 Motors  →  3 Simulate  →  4 Test & readiness  →  4b Launch day  →  5 Fly (ground station)  →  6 Analyse  →  next revision
```

Every number is labelled **MEASURED**, **SIMULATED**, **ESTIMATED**, **DERIVED** or
**HYPOTHETICAL**. Raw flight data is write-once, and a flown configuration is locked.

## 0. Install and create a project

```bash
pip install -e ".[dev]"
cmake -S firmware -B firmware/build && cmake --build firmware/build   # C flight software (for SIL)
(cd ui && npm install && npm run build)                                # web app

aerodyne init ~/rockets/my-project --name "My project" --author "Your name"
aerodyne app ~/rockets/my-project          # open http://127.0.0.1:8765
```

A project is a plain folder, so you can keep it in git or on a USB stick for the field laptop:

| Path | Contents |
|---|---|
| `registry.json` | vehicles, revisions (REV-A, REV-B…), flown configurations |
| `motors.json` | motor datasets, each with source, date and data quality |
| `missions/` | simulation setups (vehicle revision + motor + site + wind + limits) |
| `runs/` | saved simulations, Monte Carlo, SIL and readiness results (with config hashes) |
| `flights/<id>/raw/` | measured data: read-only, SHA-256 recorded, never overwritten |
| `captures/` | every ground-station session, recorded byte for byte |

`aerodyne init` adds an example vehicle and a *synthetic* motor so you can learn the tools.
Synthetic data can never pass readiness.

## 1. Design

**Design → + New** starts from a minimal rocket, the example, or an **OpenRocket `.ork`** file.
Components are edited in place, and the side profile, mass, CG, CP and static margin update
as you type. Pick the motor for analysis at the top to see loaded and burnout stability.

* **Weigh as you build.** Enter the scale reading in *Measured mass* (and *Measured CG*
  when you have it). The badge changes from ESTIMATED to MEASURED, and readiness warns
  until every part is weighed.
* **Revisions.** An ACTIVE revision can be edited freely. *New revision…* freezes it and saves
  your changes as the next one, with a change note. After a flight, that revision is FLOWN and
  locked forever; *Edit as new revision* continues from it.
* **CAD.** *Add from CAD* takes a STEP or STL file plus material density, the model axis that
  points aft, and the station of the model origin. It gives exact mass, CG and inertia
  (ESTIMATED until weighed). STEP needs `pip install ".[cad]"`.
* **OpenRocket.** Import an `.ork` when creating a vehicle. Check it with
  `aerodyne validate-ork my-design.ork`. If the file was saved after running a simulation in
  OpenRocket, this compares AERODYNE's dry mass, CG, CP and drag with OpenRocket's own
  numbers. Freeform and elliptical fins are imported as equal-area trapezoids (flagged
  ESTIMATED). Pods and parallel stages (side boosters) import as external bodies: each copy adds
  its mass, inertia, lift and drag, and the main airframe's length and reference diameter are
  unchanged. Booster motors are listed, but booster thrust and separation are not simulated yet.
  Tube fins import with their true tube mass; their lift is estimated as flat fins of the same
  side-on size, and they are left out of the flat-plate flutter check. OpenRocket materials that
  AERODYNE doesn't know are kept with the design.

## 2. Motors

**Static tests.** If you static-test commercial motors, *Characterize a static test* reads the
raw load-cell log (CSV; N, lbf or kgf; s or ms). It removes the tare, finds the burn window and
reports impulse with a 1σ uncertainty (calibration plus noise). It can save the result as a
**MEASURED** dataset, with the raw log's SHA-256 recorded in its source.

**Find motors on ThrustCurve.org** searches by name, impulse class, diameter or maker. *Import*
downloads the best curve: the certifying organisation's data if ThrustCurve has it, otherwise the
manufacturer's. User-contributed curves come in as UNKNOWN, so they can't pass readiness. The
source (simfile ID and link) is recorded with the dataset. This needs internet; at the field,
import a saved `.eng` file instead.

Import the **certified or manufacturer** thrust curve (RASP `.eng`, for example from
ThrustCurve.org) for every motor you might fly. Declare its quality and source honestly.
Several datasets for one motor sit side by side and are never merged. Readiness fails on
`HYPOTHETICAL` or `UNKNOWN` motor data.

## 3. Simulate

A **mission** is one planned flight: vehicle revision, motor, launch site (altitude, rail
length, angle and azimuth), wind and atmosphere, and **limits**. Set the limits from your
safety code, waiver and field: minimum stability, rail-exit speed, thrust-to-weight,
altitude ceiling, recovery-field radius and descent rates.

* **Wind.** Choose *Measured / forecast profile* to enter wind by altitude, or import a
  forecast or sounding CSV (altitude, speed, direction; ft/m, kt/mph/m/s; AGL or MSL). The
  Monte Carlo then disperses that profile instead of a generic one.
* **Simulate** runs the 6-DOF prediction: apogee, speeds, loads, stability over the burn,
  deployment speeds and landing point.
* **Monte Carlo** disperses mass, CG, impulse, burn time, drag, wind, temperature and rail
  pointing, then reports percentiles, an apogee histogram and the landing ellipse against your
  field radius.
* Above about Mach 0.8, import RASAero/CFD aero tables (Python API); the analytical model is
  weakest there.

### Launch simulator (animated flight with weather)

**Launch simulator** (or *Fly this motor in the launch simulator* on the Motors page) draws your
designed rocket on the pad rail and flies it from ignition to touchdown.

1. **Set the launch location.**
   * **My location** asks the device's location services for your position (GPS on a phone or
     tablet). It then loads the current weather there: temperature, humidity, station pressure,
     wind and gusts at 10 m, and wind aloft at 80, 120 and 180 m. The pad altitude comes from the
     GPS when it's accurate, otherwise from terrain height.
   * **Manual entry** always works, including with no signal or internet. Type latitude and
     longitude (decimal `40.1234, -105.2`, or `40°7'24"N`, or `40 7.404 N`) and the pad altitude.
     Then either press **Live weather here** or enter the weather by hand.
   * Browsers share location only with pages on `https://` or on the same computer
     (`localhost`). A tablet opening the laptop's app over the field network must enter
     coordinates by hand.
   * Live weather comes from the Open-Meteo forecast model, so it's labelled **ESTIMATED**
     (model analysis for that place and hour, not a measurement). Editing any weather value by
     hand overrides it. Unedited live weather is fetched again at START COUNTDOWN if it is more
     than 10 minutes old. Check it against a pad anemometer and thermometer.
   * The results give the pad and predicted landing coordinates, with an "open in maps" link
     for recovery.
2. Pick the mission and motor. Set the weather: wind speed and direction, gusts, temperature,
   humidity and station pressure, plus rail angle and pointing ("into the wind" is the default).
   Presets cover a standard calm day, a light breeze, a hot humid afternoon and a cold windy morning.
   The panel shows the resulting air density and density altitude.
3. Tick the pad checklist, **ARM** (it needs a valid location too), set the countdown length and
   **START COUNTDOWN**. **HOLD**, **RESUME** and **ABORT** work as at a real pad.
4. At T-0 the flight plays: flame and smoke while the motor burns, weathercocking in the wind,
   event call-outs (rail exit, burnout, apogee, deployments), parachutes and drift. Playback
   runs at 1–25×, and you can scrub or jump to any event. Live tiles show altitude, speeds,
   Mach, acceleration, downrange and the wind at the rocket's height, with a top-view track.
5. **Simulated results** compare this weather against a standard calm day with a vertical rail.
   The difference is the weather's effect on apogee, speeds, time to apogee, flight time and
   landing distance.

**Two views.** *Illustrated* is a side-on drawing: rail and pad, smoke drifting downwind,
flame, parachutes and a T+/altitude/speed overlay. The rocket is drawn larger than scale when
zoomed out so it stays visible. *Earth 3D* flies the same simulation over the real terrain at
the launch location:

* **Cameras:** *Pad camera* stands about 90 m from the pad across the wind and tracks the rocket
  up and back down. *Chase* follows it. *Overview* shows the whole trajectory, the pad and the
  predicted landing point; drag to orbit.
* **Map:** *Satellite* (Esri World Imagery), *Street map* (OpenStreetMap), *Terrain only* (works
  offline from cached terrain), or *Google 3D*: Google's photorealistic 3D Earth. Google 3D needs
  your own Google Maps Platform API key with the Map Tiles API enabled (it has a free monthly
  allowance). The key is stored only in your browser.
* **Offline at the field:** press *Cache terrain* at home to download the elevation around the
  site (about 4 km). The cache lives in `~/.cache/aerodyne/tiles`; set `AERODYNE_TILE_CACHE` to
  move it. Satellite imagery is not cached, so without internet the view falls back to
  sun-shaded terrain.

Weather changes the flight physically: temperature, pressure and humidity set the air density,
which affects drag and Mach number. Wind follows a power-law profile with height, plus random
gusts. Everything here is **SIMULATED**, and the checklist is a rehearsal aid, not a substitute
for your RSO's.

## 4. Test & readiness (before you build)

* **Fault suite:** your flight software (the C firmware application by default) flies
  14 scenarios on the mission's simulated trajectory. They include sensor failures, spikes,
  stale data, corrupted telemetry, low battery, clock jumps, storage failure and a processor
  reset. Each scenario must reach the right flight states at the right times.
* **Readiness review:** GO / NO-GO with the value, requirement and evidence for every check.
  It only uses Monte Carlo and SIL results computed for *exactly* the current design, motor
  and mission. Change anything and they must be re-run.

* **Fin flutter:** every fin set is checked along the simulated flight with the NACA TN 4197
  method (flutter speed against airspeed at each altitude). PASS needs a 1.5× margin. The result
  depends strongly on the material's shear modulus. Typical values are built in (G10 and
  fiberglass 2.9 GPa, carbon fibre 5 GPa, aluminium 26 GPa, birch plywood 0.62 GPa). Enter your
  laminate's own value on the fin set when you know it.

Readiness is an engineering aid. The RSO makes the final call under your safety code.

## 4b. Launch day: safety code, flight card, checklist

**Launch day** turns a mission into what you hand the RSO:

1. **Flyer:** your name, organisation, member number and certification level. They are saved
   in the project.
2. **Conditions at the pad:** press *Live weather at the mission site* for wind, gusts, cloud
   cover and visibility (ESTIMATED, from a forecast model), or type them in.
3. **Check and build flight card** runs the mission and reviews it against the quantitative
   items of the NAR/Tripoli high-power safety code and FAA Part 101:
   * a certified commercial motor, and a flyer certified for its impulse (L1 up to 640 N·s,
     L2 up to 5,120 N·s, L3 up to 40,960 N·s);
   * a stable rocket at your mission's minimum margin;
   * liftoff weight no more than ⅓ of the motor's average thrust;
   * launcher within 20° of vertical, and wind no more than 20 mph (gusts included);
   * cloud cover no more than 5/10 and visibility at least 5 miles (14 CFR 101.25);
   * apogee under the waiver ceiling;
   * landing energy (a 75 ft·lbf guideline) and fin flutter margin;
   * the minimum personnel distance from the NAR table (tick *complex rocket* for clusters and
     staged rockets).
4. **Flight card:** rocket, motor, predicted flight, recovery and safety results, with signature
   lines for the RSO. *Print* prints just the card.
5. **Pre-flight checklist:** recovery, avionics, airframe, pad and post-flight items. The ticks
   are kept per mission in this browser.

These are checklist aids. Your club's code, the range, the FAA waiver and the RSO take
precedence.

## 5. Fly: launch day with the ground station

1. **Analyse → + New** creates the flight record: flight ID, mission, the revision and motor
   actually flown, and your flight-computer hardware and firmware versions. This locks the
   revision.
2. **Fly → Start a session:**
   * *Radio on serial port*: the ground radio's serial device and baud rate
     (`pip install pyserial`).
   * *Radio bridge over UDP*: any bridge that forwards raw radio bytes as UDP datagrams.
   * *Rehearsal*: a simulated flight of the mission. It's useful for crew training and is
     clearly marked SIMULATED.
   * *Record into flight*: select the flight record. Enter the main-deploy altitude and descent
     rate so the landing estimate can use them.
3. **On the pad**, *Pad status* shows the telemetry link, armed state, IMU/baro/storage health,
   battery and GPS fix.
4. **In flight** you get live altitude, velocity, acceleration, tilt, the ground track and the
   landing estimate. The estimate's radius reflects GPS quality and extrapolation time. No
   estimate is shown when the altitude isn't baro-aided.
5. **Stop & file recording.** The raw capture is added to the flight's write-once data with
   its SHA-256. Rehearsal data is never filed as flight data.

To use a tablet at the field, run `aerodyne app <project> --host 0.0.0.0` on a laptop.
Only do this on a network you trust: the app has no login.

## 6. Analyse (and improve)

Open the flight and pick a raw file:

* **Altimeter or flight-computer logs (CSV/TXT):** the header is sniffed, and columns and
  units (ft/m, ms/s, g, hPa…) are proposed. **Confirm every column**, then *Analyse flight*.
  Logs without an accelerometer work too (baro-only reconstruction).
* **Ground-station captures (`.cap`):** decoded with the same TELEMETRY-2 receiver.

You get the reconstructed flight next to the prediction, a comparison table, **possible
contributors** (evidence, not blame) and the digital-twin validation status.
*Propose drag calibration* estimates the drag scale that would reproduce the measured apogee.
It's a proposal to review, because mass, motor or wind can mimic a drag error.

**Adopt as new revision** (after reviewing the contributors) closes the digital-twin loop.
The flown revision stays locked. A new revision carries the calibration and its provenance
(source flight, method, date). Every later simulation of that revision uses it, and the Design
page shows the vehicle's twin history: each flight, its validation status and apogee error,
and the calibrations adopted.

Then fly the calibrated revision and repeat.

## Flight computer

The firmware core (`firmware/`) is portable C11 with a HAL. See [FIRMWARE.md](FIRMWARE.md) for
the task table, state machine and telemetry format. Until it runs on your board, you can use
any flight computer: record its log and import it in step 6. Its telemetry can feed the ground
station if it speaks TELEMETRY-2 (see `aerodyne.avionics.telemetry`).
