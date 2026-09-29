# Flight computer firmware

## Hardware architecture (reference)

```text
MCU (Cortex-M4F/M7 class, FPU, >= 256 KB RAM)
├── IMU           SPI   (accel ±40 g high-g + ±16 g low-g, gyro ±2000 °/s)
├── Barometer     SPI   (static port, vented avionics bay)
├── GNSS          UART  (≥ 10 Hz, "airborne <4g" dynamic model)
├── Storage       SPI/QSPI NOR flash (+ SD for bulk)
├── Radio         UART/SPI (licensed/ISM as locally permitted)
├── Battery ADC, MCU temperature
├── FRAM / backup SRAM  (non-volatile state for reset recovery)
├── Independent watchdog
└── CAN (optional, for multi-board avionics)
```

Sensor redundancy: the sensor task supports multiple instances per kind; the
validator scores each and the navigation layer uses the healthy ones.

## Software structure

| Layer | Files |
|---|---|
| HAL (bus primitives, time, watchdog, NV store) | `include/aero_hal.h` (implemented per board) |
| Sensor validation | `aero_sensor.[ch]` |
| State estimation | `aero_kalman.[ch]` |
| Flight state machine | `aero_fsm.[ch]` |
| Telemetry | `aero_telemetry.[ch]` |
| Identity / versioning | `aero_version.[ch]` (commit + build time injected by CMake) |
| Application cycle (100 Hz) | `aero_app.[ch]`: validate → level/attitude → Kalman → FSM → health → telemetry; NV state for reset recovery |
| RTOS task table | `aero_tasks.h` |

The core is pure C11 with no allocation or I/O, built with
`-Wall -Wextra -Wpedantic -Wconversion -Werror`. It is compiled three ways:
static library (target), shared library (SIL via Python ctypes), and host unit
tests (`tests/test_firmware.c`).

## RTOS tasks (FreeRTOS)

| Task | Period | Deadline | Prio | On deadline miss |
|---|---|---|---|---|
| sensor | 10 ms | 5 ms | 6 | withhold watchdog → reset → resume |
| navigation | 10 ms | 8 ms | 5 | watchdog |
| state_estimation | 10 ms | 9 ms | 5 | watchdog |
| flight_state | 10 ms | 10 ms | 5 | watchdog |
| logging | 20 ms | 50 ms | 3 | mark storage degraded, continue flying |
| telemetry | 100 ms | 100 ms | 2 | log event |
| health_monitor | 100 ms | 100 ms | 4 | watchdog |
| command | 50 ms | 100 ms | 2 | log event |
| diagnostics | 1 s | 1 s | 1 | log event |

Tasks communicate through fixed-size queues; the flight-state task never blocks
on logging or telemetry. The watchdog is kicked only when every critical task
has checked in during the last window.

## Flight state machine

```text
SAFE ─cmd→ PREFLIGHT ─cmd arm + preflight READY→ ARMED ─liftoff→ ASCENT ─burnout→ COAST ─apogee→ DESCENT ─landed→ LANDED
  ↑______cmd safe/disarm______|__________________________|   (no path back from any flight state)
```

| Transition | Evidence (all debounced; one sample never suffices) |
|---|---|
| ARMED→ASCENT | axial accel > 2 g for 0.1 s, **or** baro > 25 m AGL for 0.3 s |
| ASCENT→COAST | axial accel < 0 for 0.1 s; or 10 s timeout; or (accel failed) filtered velocity < 0 |
| COAST→DESCENT | ≥ 3 s after liftoff **and** filtered velocity < 0 for 0.2 s **and** baro 3 m below peak for 0.2 s; baro ignored above 240 m/s; if baro is degraded, velocity alone |
| DESCENT→LANDED | |v| < 2 m/s and < 50 m AGL for 5 s (baro/filter); if baro degraded, GNSS altitude/velocity; 900 s timeout |

A processor reset restores the state, liftoff/apogee times, ground reference,
filter state, attitude and telemetry sequence from NV storage (`RESET_RECOVERY`
event), so a reset in flight cannot re-arm or re-detect liftoff.

## Firmware identity

Every build carries version, build timestamp and commit hash (injected by CMake).
At boot, the image SHA-256 and configuration SHA-256 are computed and provisioned
with `aero_app_set_identity_hashes()`. The application then transmits an **identity
frame** (TELEMETRY-2, sync `0xD2AE`, 117 bytes, CRC-16) every 5 s between telemetry
frames: vehicle/flight IDs, firmware version, commit, image hash, config hash and
hardware version.

The ground station verifies the reported image hash against the flight record
(*Pad status → Firmware identity*): **PASS** on a match, **FAIL** on a mismatch or no
identity heard, **WARN** when hashes are not provisioned or the flight record has no
expected hash. All values are stored with the flight record.

## Verification

* `firmware/tests/test_firmware.c` - host unit tests (FSM, filter, telemetry, validator, app arming, reset recovery, stuck-sensor detection).
* `aerodyne sil --all-faults --backend c-app` - the complete C application flies all 14 fault scenarios through the Python SIL harness (ctypes); `pytest` runs the same.
* The C state machine and telemetry encoder are also checked transition-for-transition and byte-for-byte against the Python reference.

Known difference from the Python reference: the C validator has no median/MAD
outlier test yet (spikes are caught by the rate-of-change check).

## Porting to hardware (next steps)

1. Implement `aero_hal.h` for the chosen MCU (vendor HAL or bare registers).
2. Write device drivers against the HAL (IMU, baro, GNSS, flash, radio).
3. Instantiate the task table with `xTaskCreateStatic` / `vTaskDelayUntil`.
4. Build with `AERO_HIL=1` and run `aerodyne.sil.hil.HILBridge` before any flight.
