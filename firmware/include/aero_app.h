/*
 * Flight-software application cycle (runs in the flight_state task at 100 Hz):
 *   validate sensors -> attitude -> Kalman filter -> state machine
 *   -> health monitor -> telemetry, with non-volatile state for reset recovery.
 *
 * Mirrors the structure of src/aerodyne/avionics/fsw.py. The SIL harness runs
 * this exact code (via ctypes) against the fault-scenario suite.
 */
#ifndef AERO_APP_H
#define AERO_APP_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "aero_fsm.h"
#include "aero_kalman.h"
#include "aero_sensor.h"
#include "aero_telemetry.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    AERO_SLOT_ACCEL = 0,
    AERO_SLOT_GYRO = 1,
    AERO_SLOT_BARO = 2,
    AERO_SLOT_GNSS = 3,
    AERO_SLOT_BATTERY = 4,
    AERO_SLOT_TEMPERATURE = 5,
    AERO_SLOT_COUNT = 6
} aero_slot_t;

/* Fault flags (aero_app_t.faults). */
enum {
    AERO_FAULT_LOW_BATTERY = 1u << 0,
    AERO_FAULT_STORAGE = 1u << 1,
    AERO_FAULT_SENSOR_BASE = 1u << 8 /* bit (8 + slot) = that sensor degraded/failed */
};

typedef struct {
    bool present;              /* a new sample arrived this cycle */
    uint32_t sequence;
    double timestamp;          /* s, flight-computer clock */
    double v[3];               /* accel/gyro xyz; gnss lat,lon,alt; scalar in v[0] */
    aero_sensor_status_t status;
    float quality;
} aero_input_t;

typedef struct {
    aero_fsm_config_t fsm;
    aero_limits_t limits[AERO_SLOT_COUNT];
    float stale_timeout[AERO_SLOT_COUNT];
    uint16_t vehicle_id;
    uint16_t flight_id;
    float telemetry_period;       /* s */
    float low_battery_v;
    float baro_gate_sigma;
    float descent_accel_sigma;
    float kf_accel_sigma, kf_baro_sigma;
} aero_app_config_t;

/* Persisted to backup SRAM / FRAM. */
typedef struct {
    uint32_t magic;
    uint32_t boot_count;
    int32_t state;
    double t_liftoff, t_apogee;
    float max_baro_alt;
    double ground_alt, gnss_ground;
    uint8_t have_ground, have_gnss_ground;
    double kf_alt, kf_vel;
    double attitude[4];
    uint32_t tlm_sequence;
} aero_nv_state_t;

#define AERO_NV_MAGIC 0xAE0D1E01u
#define AERO_GNSS_HIST 64

typedef struct {
    aero_app_config_t cfg;
    aero_fsm_t fsm;
    aero_kf_t kf;
    aero_validator_t val[AERO_SLOT_COUNT];
    aero_health_t health[AERO_SLOT_COUNT];
    bool have_sample[AERO_SLOT_COUNT];
    double last_sample_t[AERO_SLOT_COUNT];
    aero_input_t latest[AERO_SLOT_COUNT];
    bool latest_valid[AERO_SLOT_COUNT];
    double q[4];
    bool leveled;
    bool have_ground, have_gnss_ground;
    double ground_alt, gnss_ground;
    float baro_agl;
    uint32_t baro_rejects;
    double gnss_t[AERO_GNSS_HIST], gnss_alt[AERO_GNSS_HIST];
    int gnss_head, gnss_count;
    bool have_t_prev;
    double t_prev;
    double next_tlm;
    uint32_t sequence;
    uint32_t faults;
    aero_cmd_t pending_cmd;
    bool preflight_ok;
    float max_est_alt;
    uint32_t boot_count;
    bool resumed;
    aero_nv_state_t nv;
    bool nv_dirty;
} aero_app_t;

void aero_app_default_config(aero_app_config_t *cfg);
/* nv may be NULL (cold start). A valid in-flight nv state resumes the flight. */
void aero_app_init(aero_app_t *app, const aero_app_config_t *cfg, const aero_nv_state_t *nv);
void aero_app_command(aero_app_t *app, aero_cmd_t cmd, bool preflight_ok);
void aero_app_report_storage(aero_app_t *app, bool ok);
/* One cycle. Returns true and fills *pkt when a telemetry frame is due.
 * After the call, app->nv holds the state to persist when app->nv_dirty. */
bool aero_app_step(aero_app_t *app, double t, const aero_input_t in[AERO_SLOT_COUNT],
                   aero_tlm_packet_t *pkt);

/* Accessors (diagnostics, SIL bindings; avoid depending on struct layout). */
size_t aero_app_sizeof(void);
size_t aero_app_nv_sizeof(void);
aero_state_t aero_app_state(const aero_app_t *app);
aero_reason_t aero_app_last_reason(const aero_app_t *app);
uint32_t aero_app_faults(const aero_app_t *app);
aero_health_t aero_app_health(const aero_app_t *app, int slot);
float aero_app_max_est_alt(const aero_app_t *app);
uint32_t aero_app_boot_count(const aero_app_t *app);
const aero_nv_state_t *aero_app_nv(const aero_app_t *app);
bool aero_app_nv_dirty(const aero_app_t *app);

#ifdef __cplusplus
}
#endif
#endif
