/*
 * AERODYNE flight state machine.
 * Behaviour is defined by, and tested against, the Python reference
 * implementation in src/aerodyne/avionics/state_machine.py.
 *
 * Pure logic: no I/O, no dynamic allocation, deterministic.
 */
#ifndef AERO_FSM_H
#define AERO_FSM_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    AERO_STATE_SAFE = 0,
    AERO_STATE_PREFLIGHT = 1,
    AERO_STATE_ARMED = 2,
    AERO_STATE_ASCENT = 3,
    AERO_STATE_COAST = 4,
    AERO_STATE_DESCENT = 5,
    AERO_STATE_LANDED = 6
} aero_state_t;

typedef enum {
    AERO_CMD_NONE = 0,
    AERO_CMD_PREFLIGHT = 1,
    AERO_CMD_ARM = 2,
    AERO_CMD_DISARM = 3,
    AERO_CMD_SAFE = 4
} aero_cmd_t;

typedef enum {
    AERO_REASON_NONE = 0,
    AERO_REASON_COMMAND,
    AERO_REASON_LIFTOFF_ACCEL,
    AERO_REASON_LIFTOFF_BARO,
    AERO_REASON_LIFTOFF_BOTH,
    AERO_REASON_BURNOUT_DECEL,
    AERO_REASON_BURNOUT_TIMEOUT,
    AERO_REASON_BURNOUT_VELOCITY,
    AERO_REASON_APOGEE_VEL_BARO,
    AERO_REASON_APOGEE_VEL_ONLY,
    AERO_REASON_LANDED_BARO,
    AERO_REASON_LANDED_GNSS,
    AERO_REASON_LANDED_TIMEOUT
} aero_reason_t;

typedef struct {
    float launch_accel;          /* m/s^2 */
    float launch_debounce;       /* s */
    float launch_baro_alt;       /* m */
    float launch_baro_debounce;  /* s */
    float burnout_accel;
    float burnout_debounce;
    float max_burn_time;
    float apogee_lockout;
    float apogee_vel_debounce;
    float apogee_baro_drop;
    float apogee_baro_debounce;
    float mach_lockout_speed;
    float landed_speed;
    float landed_debounce;
    float landed_alt_band;
    float landed_timeout;
} aero_fsm_config_t;

typedef struct {
    double t;
    float accel_axial;
    bool accel_ok;
    float baro_alt_agl;
    bool baro_ok;
    float est_alt_agl;
    float est_vel;
    bool gnss_ok;
    float gnss_alt_agl;
    float gnss_vel;
    aero_cmd_t command;
    bool preflight_ok;
} aero_fsm_inputs_t;

enum { AERO_TIMER_COUNT = 7 };

typedef struct {
    aero_fsm_config_t cfg;
    aero_state_t state;
    double t_liftoff;          /* NAN until liftoff */
    double t_apogee;           /* NAN until apogee */
    float max_baro_alt;
    double timer_start[AERO_TIMER_COUNT];
    bool timer_running[AERO_TIMER_COUNT];
    aero_reason_t last_reason;
    uint32_t transition_count;
} aero_fsm_t;

void aero_fsm_default_config(aero_fsm_config_t *cfg);
void aero_fsm_init(aero_fsm_t *fsm, const aero_fsm_config_t *cfg);
/* Resume after a processor reset from persisted state. */
void aero_fsm_restore(aero_fsm_t *fsm, aero_state_t state, double t_liftoff,
                      float max_baro_alt, double t_apogee);
/* Advance one cycle. Returns the (possibly new) state. */
aero_state_t aero_fsm_step(aero_fsm_t *fsm, const aero_fsm_inputs_t *in);
const char *aero_state_name(aero_state_t s);

#ifdef __cplusplus
}
#endif
#endif
