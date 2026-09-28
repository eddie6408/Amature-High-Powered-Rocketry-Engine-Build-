#include "aero_fsm.h"

#include <math.h>
#include <string.h>

#define G0 9.80665f

enum { T_LAUNCH_ACC, T_LAUNCH_BARO, T_BURNOUT, T_BO_VEL, T_APO_VEL, T_APO_BARO, T_LANDED };

void aero_fsm_default_config(aero_fsm_config_t *c)
{
    c->launch_accel = 2.0f * G0;
    c->launch_debounce = 0.10f;
    c->launch_baro_alt = 25.0f;
    c->launch_baro_debounce = 0.30f;
    c->burnout_accel = 0.0f;
    c->burnout_debounce = 0.10f;
    c->max_burn_time = 10.0f;
    c->apogee_lockout = 3.0f;
    c->apogee_vel_debounce = 0.20f;
    c->apogee_baro_drop = 3.0f;
    c->apogee_baro_debounce = 0.20f;
    c->mach_lockout_speed = 240.0f;
    c->landed_speed = 2.0f;
    c->landed_debounce = 5.0f;
    c->landed_alt_band = 50.0f;
    c->landed_timeout = 900.0f;
}

static void clear_timers(aero_fsm_t *f)
{
    memset(f->timer_running, 0, sizeof f->timer_running);
}

void aero_fsm_init(aero_fsm_t *f, const aero_fsm_config_t *cfg)
{
    memset(f, 0, sizeof *f);
    if (cfg) {
        f->cfg = *cfg;
    } else {
        aero_fsm_default_config(&f->cfg);
    }
    f->state = AERO_STATE_SAFE;
    f->t_liftoff = NAN;
    f->t_apogee = NAN;
    f->max_baro_alt = -1e9f;
}

void aero_fsm_restore(aero_fsm_t *f, aero_state_t state, double t_liftoff, float max_baro_alt,
                      double t_apogee)
{
    f->state = state;
    f->t_liftoff = t_liftoff;
    f->t_apogee = t_apogee;
    f->max_baro_alt = max_baro_alt;
    clear_timers(f);
}

/* Debounce: true once `cond` has held continuously for `duration` seconds. */
static bool sustained(aero_fsm_t *f, int id, bool cond, double t, float duration)
{
    if (!cond) {
        f->timer_running[id] = false;
        return false;
    }
    if (!f->timer_running[id]) {
        f->timer_running[id] = true;
        f->timer_start[id] = t;
        return duration <= 0.0f;
    }
    /* 1 us tolerance absorbs float32 config rounding (0.1f != 0.1) */
    return (t - f->timer_start[id]) >= (double)duration - 1e-6;
}

static void go(aero_fsm_t *f, aero_state_t s, aero_reason_t why)
{
    f->state = s;
    f->last_reason = why;
    f->transition_count++;
    clear_timers(f);
}

static bool in_flight(aero_state_t s)
{
    return s == AERO_STATE_ASCENT || s == AERO_STATE_COAST || s == AERO_STATE_DESCENT;
}

aero_state_t aero_fsm_step(aero_fsm_t *f, const aero_fsm_inputs_t *in)
{
    const aero_fsm_config_t *c = &f->cfg;
    if (in->baro_ok && in_flight(f->state) && in->baro_alt_agl > f->max_baro_alt) {
        f->max_baro_alt = in->baro_alt_agl;
    }

    switch (f->state) {
    case AERO_STATE_SAFE:
        if (in->command == AERO_CMD_PREFLIGHT) go(f, AERO_STATE_PREFLIGHT, AERO_REASON_COMMAND);
        break;

    case AERO_STATE_PREFLIGHT:
        if (in->command == AERO_CMD_ARM) {
            if (in->preflight_ok) go(f, AERO_STATE_ARMED, AERO_REASON_COMMAND);
        } else if (in->command == AERO_CMD_SAFE || in->command == AERO_CMD_DISARM) {
            go(f, AERO_STATE_SAFE, AERO_REASON_COMMAND);
        }
        break;

    case AERO_STATE_ARMED: {
        if (in->command == AERO_CMD_DISARM || in->command == AERO_CMD_SAFE) {
            go(f, AERO_STATE_SAFE, AERO_REASON_COMMAND);
            break;
        }
        bool acc = sustained(f, T_LAUNCH_ACC, in->accel_ok && in->accel_axial > c->launch_accel,
                             in->t, c->launch_debounce);
        bool baro = sustained(f, T_LAUNCH_BARO,
                              in->baro_ok && in->baro_alt_agl > c->launch_baro_alt, in->t,
                              c->launch_baro_debounce);
        if (acc || baro) {
            f->t_liftoff = in->t - (acc ? c->launch_debounce : c->launch_baro_debounce);
            go(f, AERO_STATE_ASCENT,
               acc && baro ? AERO_REASON_LIFTOFF_BOTH
                           : (acc ? AERO_REASON_LIFTOFF_ACCEL : AERO_REASON_LIFTOFF_BARO));
        }
        break;
    }

    case AERO_STATE_ASCENT: {
        double since = in->t - (isnan(f->t_liftoff) ? in->t : f->t_liftoff);
        bool bo = sustained(f, T_BURNOUT, in->accel_ok && in->accel_axial < c->burnout_accel,
                            in->t, c->burnout_debounce);
        if (bo) {
            go(f, AERO_STATE_COAST, AERO_REASON_BURNOUT_DECEL);
        } else if (since > c->max_burn_time) {
            go(f, AERO_STATE_COAST, AERO_REASON_BURNOUT_TIMEOUT);
        } else if (!in->accel_ok &&
                   sustained(f, T_BO_VEL, in->est_vel < 0.0f, in->t, c->apogee_vel_debounce)) {
            go(f, AERO_STATE_COAST, AERO_REASON_BURNOUT_VELOCITY);
        }
        break;
    }

    case AERO_STATE_COAST: {
        double since = in->t - (isnan(f->t_liftoff) ? in->t : f->t_liftoff);
        bool vel = sustained(f, T_APO_VEL, in->est_vel < 0.0f, in->t, c->apogee_vel_debounce);
        bool baro_usable = in->baro_ok && fabsf(in->est_vel) < c->mach_lockout_speed;
        bool baro = sustained(f, T_APO_BARO,
                              baro_usable &&
                                  in->baro_alt_agl < f->max_baro_alt - c->apogee_baro_drop,
                              in->t, c->apogee_baro_debounce);
        if (since >= c->apogee_lockout) {
            if (in->baro_ok) {
                if (vel && baro) {
                    f->t_apogee = in->t;
                    go(f, AERO_STATE_DESCENT, AERO_REASON_APOGEE_VEL_BARO);
                }
            } else if (vel) {
                f->t_apogee = in->t;
                go(f, AERO_STATE_DESCENT, AERO_REASON_APOGEE_VEL_ONLY);
            }
        }
        break;
    }

    case AERO_STATE_DESCENT: {
        bool still;
        aero_reason_t why;
        if (in->baro_ok) {
            still = fabsf(in->est_vel) < c->landed_speed && in->est_alt_agl < c->landed_alt_band;
            why = AERO_REASON_LANDED_BARO;
        } else {
            still = in->gnss_ok && fabsf(in->gnss_vel) < c->landed_speed &&
                    in->gnss_alt_agl < 2.0f * c->landed_alt_band;
            why = AERO_REASON_LANDED_GNSS;
        }
        if (sustained(f, T_LANDED, still, in->t, c->landed_debounce)) {
            go(f, AERO_STATE_LANDED, why);
        } else if (!isnan(f->t_apogee) && in->t - f->t_apogee > c->landed_timeout) {
            go(f, AERO_STATE_LANDED, AERO_REASON_LANDED_TIMEOUT);
        }
        break;
    }

    case AERO_STATE_LANDED:
    default:
        break;
    }
    return f->state;
}

const char *aero_state_name(aero_state_t s)
{
    static const char *names[] = {"SAFE",  "PREFLIGHT", "ARMED", "ASCENT",
                                  "COAST", "DESCENT",   "LANDED"};
    return ((unsigned)s < sizeof names / sizeof names[0]) ? names[s] : "INVALID";
}
