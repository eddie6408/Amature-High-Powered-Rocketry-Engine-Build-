#include "aero_app.h"

#include <math.h>
#include <string.h>

#define G0 9.80665

/* ---------------------------------------------------------------- quaternion */
static void q_norm(double q[4])
{
    double n = sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]);
    if (n > 0.0) {
        for (int i = 0; i < 4; i++) q[i] /= n;
    }
}

static void q_mul(const double a[4], const double b[4], double o[4])
{
    double r[4];
    r[0] = a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3];
    r[1] = a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2];
    r[2] = a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1];
    r[3] = a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0];
    memcpy(o, r, sizeof r);
}

/* body -> world rotation of vector v */
static void q_rotate(const double q[4], const double v[3], double o[3])
{
    double w = q[0], x = q[1], y = q[2], z = q[3];
    o[0] = (1 - 2 * (y * y + z * z)) * v[0] + 2 * (x * y - w * z) * v[1] + 2 * (x * z + w * y) * v[2];
    o[1] = 2 * (x * y + w * z) * v[0] + (1 - 2 * (x * x + z * z)) * v[1] + 2 * (y * z - w * x) * v[2];
    o[2] = 2 * (x * z - w * y) * v[0] + 2 * (y * z + w * x) * v[1] + (1 - 2 * (x * x + y * y)) * v[2];
}

/* shortest-arc rotation taking unit(a) onto +Z */
static void q_level(const double a[3], double q[4])
{
    double n = sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]);
    double u[3] = {a[0] / n, a[1] / n, a[2] / n};
    double c = u[2];
    if (c < -0.999999) {
        q[0] = 0.0; q[1] = 1.0; q[2] = 0.0; q[3] = 0.0;
        return;
    }
    /* cross(u, z) = (u1, -u0, 0) */
    q[0] = 1.0 + c;
    q[1] = u[1];
    q[2] = -u[0];
    q[3] = 0.0;
    q_norm(q);
}

static void q_propagate(double q[4], const double w[3], double dt)
{
    double rate = sqrt(w[0] * w[0] + w[1] * w[1] + w[2] * w[2]);
    double ang = rate * dt;
    if (ang < 1e-12) return;
    double s = sin(ang / 2) / rate;
    double dq[4] = {cos(ang / 2), w[0] * s, w[1] * s, w[2] * s};
    q_mul(q, dq, q);
    q_norm(q);
}

/* -------------------------------------------------------------------- config */
void aero_app_default_config(aero_app_config_t *c)
{
    memset(c, 0, sizeof *c);
    aero_fsm_default_config(&c->fsm);
    /* {min, max, max_rate, stuck_count, degrade_after, fail_after, recover_after} */
    const aero_limits_t accel = {-400.0f, 400.0f, 20000.0f, 50, 1, 20, 10};
    const aero_limits_t gyro = {-35.0f, 35.0f, 5000.0f, 0, 1, 20, 10};
    const aero_limits_t baro = {-500.0f, 30000.0f, 1500.0f, 100, 1, 20, 10};
    const aero_limits_t gnss = {-500.0f, 30000.0f, 2000.0f, 0, 1, 10, 10};
    const aero_limits_t batt = {0.0f, 20.0f, 50.0f, 0, 1, 5, 10};
    const aero_limits_t temp = {-60.0f, 125.0f, 50.0f, 0, 1, 5, 10};
    c->limits[AERO_SLOT_ACCEL] = accel;
    c->limits[AERO_SLOT_GYRO] = gyro;
    c->limits[AERO_SLOT_BARO] = baro;
    c->limits[AERO_SLOT_GNSS] = gnss;
    c->limits[AERO_SLOT_BATTERY] = batt;
    c->limits[AERO_SLOT_TEMPERATURE] = temp;
    const float stale[AERO_SLOT_COUNT] = {0.05f, 0.05f, 0.2f, 2.0f, 2.0f, 5.0f};
    memcpy(c->stale_timeout, stale, sizeof stale);
    c->vehicle_id = 1;
    c->flight_id = 1;
    c->telemetry_period = 0.1f;
    c->low_battery_v = 7.0f;
    c->baro_gate_sigma = 6.0f;
    c->descent_accel_sigma = 8.0f;
    c->kf_accel_sigma = 2.0f;
    c->kf_baro_sigma = 1.5f;
}

static bool ground_state(aero_state_t s)
{
    return s == AERO_STATE_SAFE || s == AERO_STATE_PREFLIGHT || s == AERO_STATE_ARMED;
}

static bool flight_state(aero_state_t s)
{
    return s == AERO_STATE_ASCENT || s == AERO_STATE_COAST || s == AERO_STATE_DESCENT;
}

static void persist(aero_app_t *a)
{
    aero_nv_state_t *n = &a->nv;
    n->magic = AERO_NV_MAGIC;
    n->boot_count = a->boot_count;
    n->state = (int32_t)a->fsm.state;
    n->t_liftoff = a->fsm.t_liftoff;
    n->t_apogee = a->fsm.t_apogee;
    n->max_baro_alt = a->fsm.max_baro_alt;
    n->ground_alt = a->ground_alt;
    n->gnss_ground = a->gnss_ground;
    n->have_ground = a->have_ground;
    n->have_gnss_ground = a->have_gnss_ground;
    n->kf_alt = a->kf.x[0];
    n->kf_vel = a->kf.x[1];
    memcpy(n->attitude, a->q, sizeof a->q);
    n->tlm_sequence = a->sequence;
    a->nv_dirty = true;
}

void aero_app_init(aero_app_t *a, const aero_app_config_t *cfg, const aero_nv_state_t *nv)
{
    memset(a, 0, sizeof *a);
    if (cfg) {
        a->cfg = *cfg;
    } else {
        aero_app_default_config(&a->cfg);
    }
    aero_fsm_init(&a->fsm, &a->cfg.fsm);
    aero_kf_init(&a->kf, a->cfg.kf_accel_sigma, a->cfg.kf_baro_sigma, 0.0);
    for (int i = 0; i < AERO_SLOT_COUNT; i++) {
        aero_validator_init(&a->val[i], &a->cfg.limits[i]);
        a->health[i] = AERO_HEALTH_UNKNOWN;
    }
    a->q[0] = 1.0;
    a->boot_count = 1;
    if (nv && nv->magic == AERO_NV_MAGIC) {
        a->boot_count = nv->boot_count + 1;
        a->sequence = nv->tlm_sequence;
        aero_state_t st = (aero_state_t)nv->state;
        if (flight_state(st) || st == AERO_STATE_LANDED) {
            aero_fsm_restore(&a->fsm, st, nv->t_liftoff, nv->max_baro_alt, nv->t_apogee);
            a->ground_alt = nv->ground_alt;
            a->have_ground = nv->have_ground;
            a->gnss_ground = nv->gnss_ground;
            a->have_gnss_ground = nv->have_gnss_ground;
            aero_kf_set_state(&a->kf, nv->kf_alt, nv->kf_vel);
            memcpy(a->q, nv->attitude, sizeof a->q);
            a->leveled = true;
            a->resumed = true;
        }
    }
    persist(a);
}

void aero_app_command(aero_app_t *a, aero_cmd_t cmd, bool preflight_ok)
{
    a->pending_cmd = cmd;
    a->preflight_ok = preflight_ok;
}

void aero_app_report_storage(aero_app_t *a, bool ok)
{
    if (!ok) a->faults |= AERO_FAULT_STORAGE;
}

static float slot_scalar(int slot, const aero_input_t *s)
{
    if (slot == AERO_SLOT_ACCEL || slot == AERO_SLOT_GYRO) {
        return (float)sqrt(s->v[0] * s->v[0] + s->v[1] * s->v[1] + s->v[2] * s->v[2]);
    }
    if (slot == AERO_SLOT_GNSS) return (float)s->v[2];   /* validate altitude */
    return (float)s->v[0];
}

static uint16_t sensor_status_word(const aero_app_t *a)
{
    /* slot order matches telemetry SENSOR_SLOTS: accel, gyro, baro, gnss, battery,
     * temperature, storage, radio */
    uint16_t w = 0;
    for (int i = 0; i < AERO_SLOT_COUNT; i++) w |= (uint16_t)((a->health[i] & 3u) << (2 * i));
    w |= (uint16_t)(((a->faults & AERO_FAULT_STORAGE) ? AERO_HEALTH_FAILED : AERO_HEALTH_OK) << 12);
    w |= (uint16_t)(AERO_HEALTH_OK << 14);
    return w;
}

static int16_t q16(double x)
{
    double v = round(x * 32767.0);
    if (v > 32767.0) v = 32767.0;
    if (v < -32767.0) v = -32767.0;
    return (int16_t)v;
}

bool aero_app_step(aero_app_t *a, double t, const aero_input_t in[AERO_SLOT_COUNT],
                   aero_tlm_packet_t *pkt)
{
    const aero_app_config_t *c = &a->cfg;
    double dt = 0.0;
    if (a->have_t_prev) dt = t - a->t_prev > 0.0 ? t - a->t_prev : 0.0;
    a->t_prev = t;
    a->have_t_prev = true;
    a->nv_dirty = false;

    /* 1. validate */
    bool fresh[AERO_SLOT_COUNT] = {false};
    for (int i = 0; i < AERO_SLOT_COUNT; i++) {
        if (!in[i].present) {
            if (a->have_sample[i] && t - a->last_sample_t[i] > c->stale_timeout[i]) {
                aero_validate(&a->val[i], NULL, t);
                a->health[i] = a->val[i].health;
                a->latest_valid[i] = false;
            }
            continue;
        }
        aero_sample_t s = {(uint16_t)i, in[i].sequence, in[i].timestamp, slot_scalar(i, &in[i]),
                           in[i].status, in[i].quality};
        uint32_t f = aero_validate(&a->val[i], &s, t);
        a->health[i] = a->val[i].health;
        a->have_sample[i] = true;
        a->last_sample_t[i] = t;
        if (f == 0) {
            fresh[i] = true;
            a->latest[i] = in[i];
            a->latest_valid[i] = true;
        } else if (a->health[i] != AERO_HEALTH_OK) {
            a->latest_valid[i] = false;
        }
    }
    aero_state_t state = a->fsm.state;
    bool accel_ok = fresh[AERO_SLOT_ACCEL] && a->health[AERO_SLOT_ACCEL] == AERO_HEALTH_OK;
    bool gyro_ok = fresh[AERO_SLOT_GYRO] && a->health[AERO_SLOT_GYRO] == AERO_HEALTH_OK;
    bool baro_ok = fresh[AERO_SLOT_BARO] && a->health[AERO_SLOT_BARO] == AERO_HEALTH_OK;

    /* 2. pad: ground reference + leveling; flight: gyro attitude */
    if (ground_state(state)) {
        if (baro_ok) {
            double b = in[AERO_SLOT_BARO].v[0];
            a->ground_alt = a->have_ground ? 0.98 * a->ground_alt + 0.02 * b : b;
            a->have_ground = true;
            aero_kf_set_state(&a->kf, 0.0, 0.0);
        }
        if (accel_ok) {
            const double *f = in[AERO_SLOT_ACCEL].v;
            if (sqrt(f[0] * f[0] + f[1] * f[1] + f[2] * f[2]) > 0.5 * G0) {
                q_level(f, a->q);
                a->leveled = true;
            }
        }
    } else if (gyro_ok && dt > 0.0) {
        q_propagate(a->q, in[AERO_SLOT_GYRO].v, dt);
    }

    /* 3. navigation */
    float accel_axial = accel_ok ? (float)in[AERO_SLOT_ACCEL].v[0] : 0.0f;
    if (dt > 0.0) {
        if (accel_ok && state != AERO_STATE_DESCENT && state != AERO_STATE_LANDED) {
            double fw[3];
            if (a->leveled) {
                q_rotate(a->q, in[AERO_SLOT_ACCEL].v, fw);
            } else {
                memcpy(fw, in[AERO_SLOT_ACCEL].v, sizeof fw);
            }
            aero_kf_predict(&a->kf, fw[2] - G0, dt, -1.0);
        } else {
            aero_kf_predict(&a->kf, 0.0, dt, c->descent_accel_sigma);
        }
    }
    if (baro_ok && a->have_ground) {
        float agl = (float)(in[AERO_SLOT_BARO].v[0] - a->ground_alt);
        bool transonic = fabs(a->kf.x[1]) > c->fsm.mach_lockout_speed;
        double nis = aero_kf_innovation(&a->kf, agl, -1.0);
        bool gated = fabs(nis) >= c->baro_gate_sigma && accel_ok && flight_state(state);
        if (gated) {
            a->baro_rejects++;
        } else {
            a->baro_agl = agl;
            if (!transonic) aero_kf_update(&a->kf, agl, -1.0);
        }
    }
    if ((float)a->kf.x[0] > a->max_est_alt) a->max_est_alt = (float)a->kf.x[0];
    bool baro_healthy = a->health[AERO_SLOT_BARO] == AERO_HEALTH_OK && a->have_ground;
    bool accel_healthy = a->health[AERO_SLOT_ACCEL] == AERO_HEALTH_OK && a->latest_valid[AERO_SLOT_ACCEL];
    if (accel_healthy && !accel_ok) accel_axial = (float)a->latest[AERO_SLOT_ACCEL].v[0];

    /* GNSS altitude: independent vertical source for landing detection */
    bool gnss_ok = fresh[AERO_SLOT_GNSS] && a->health[AERO_SLOT_GNSS] == AERO_HEALTH_OK;
    if (gnss_ok) {
        double galt = in[AERO_SLOT_GNSS].v[2];
        if (ground_state(state)) {
            a->gnss_ground = a->have_gnss_ground ? 0.9 * a->gnss_ground + 0.1 * galt : galt;
            a->have_gnss_ground = true;
        }
        int idx = (a->gnss_head + a->gnss_count) % AERO_GNSS_HIST;
        if (a->gnss_count == AERO_GNSS_HIST) {
            a->gnss_head = (a->gnss_head + 1) % AERO_GNSS_HIST;
        } else {
            a->gnss_count++;
        }
        a->gnss_t[idx] = t;
        a->gnss_alt[idx] = galt;
    }
    while (a->gnss_count > 0 && t - a->gnss_t[a->gnss_head] > 5.0) {
        a->gnss_head = (a->gnss_head + 1) % AERO_GNSS_HIST;
        a->gnss_count--;
    }
    bool gnss_valid = false;
    float gnss_agl = 0.0f, gnss_vel = 0.0f;
    if (a->health[AERO_SLOT_GNSS] == AERO_HEALTH_OK && a->have_gnss_ground && a->gnss_count >= 2) {
        int last = (a->gnss_head + a->gnss_count - 1) % AERO_GNSS_HIST;
        double span = a->gnss_t[last] - a->gnss_t[a->gnss_head];
        if (span >= 4.0) {
            gnss_valid = true;
            gnss_vel = (float)((a->gnss_alt[last] - a->gnss_alt[a->gnss_head]) / span);
            gnss_agl = (float)(a->gnss_alt[last] - a->gnss_ground);
        }
    }

    /* 4. state machine */
    aero_fsm_inputs_t fi;
    memset(&fi, 0, sizeof fi);
    fi.t = t;
    fi.accel_axial = accel_axial;
    fi.accel_ok = accel_healthy;
    fi.baro_alt_agl = a->baro_agl;
    fi.baro_ok = baro_healthy;
    fi.est_alt_agl = (float)a->kf.x[0];
    fi.est_vel = (float)a->kf.x[1];
    fi.gnss_ok = gnss_valid;
    fi.gnss_alt_agl = gnss_agl;
    fi.gnss_vel = gnss_vel;
    fi.command = a->pending_cmd;
    fi.preflight_ok = a->preflight_ok;
    aero_state_t prev = a->fsm.state;
    aero_state_t now = aero_fsm_step(&a->fsm, &fi);
    a->pending_cmd = AERO_CMD_NONE;
    if (now != prev || (flight_state(now) && floor(t * 10.0) != floor((t - dt) * 10.0))) {
        persist(a);
    }

    /* 5. health monitor */
    if (a->latest_valid[AERO_SLOT_BATTERY] && a->latest[AERO_SLOT_BATTERY].v[0] < c->low_battery_v) {
        a->faults |= AERO_FAULT_LOW_BATTERY;
    }
    for (int i = 0; i < AERO_SLOT_COUNT; i++) {
        if (a->health[i] == AERO_HEALTH_DEGRADED || a->health[i] == AERO_HEALTH_FAILED) {
            a->faults |= AERO_FAULT_SENSOR_BASE << i;
        }
    }

    /* 6. telemetry */
    if (t + 1e-9 < a->next_tlm || pkt == NULL) return false;
    a->next_tlm = t + c->telemetry_period;
    a->sequence++;
    a->nv.tlm_sequence = a->sequence;
    a->nv_dirty = true;
    memset(pkt, 0, sizeof *pkt);
    pkt->vehicle_id = c->vehicle_id;
    pkt->flight_id = c->flight_id;
    pkt->sequence = a->sequence;
    pkt->timestamp_ms = (uint32_t)(int64_t)(t * 1000.0);
    pkt->altitude = (float)a->kf.x[0];
    pkt->velocity = (float)a->kf.x[1];
    pkt->acceleration = a->latest_valid[AERO_SLOT_ACCEL] ? (float)a->latest[AERO_SLOT_ACCEL].v[0] : NAN;
    if (a->latest_valid[AERO_SLOT_GNSS] && a->health[AERO_SLOT_GNSS] == AERO_HEALTH_OK) {
        pkt->lat_e7 = (int32_t)round(a->latest[AERO_SLOT_GNSS].v[0] * 1e7);
        pkt->lon_e7 = (int32_t)round(a->latest[AERO_SLOT_GNSS].v[1] * 1e7);
        pkt->gnss_fix = 3;
        pkt->gnss_sats = (uint8_t)lroundf(a->latest[AERO_SLOT_GNSS].quality * 12.0f);
    }
    for (int i = 0; i < 4; i++) pkt->attitude[i] = q16(a->q[i]);
    if (a->latest_valid[AERO_SLOT_BATTERY]) {
        pkt->battery_mv = (uint16_t)(a->latest[AERO_SLOT_BATTERY].v[0] * 1000.0);
    }
    if (a->latest_valid[AERO_SLOT_TEMPERATURE]) {
        pkt->temperature_c10 = (int16_t)lround(a->latest[AERO_SLOT_TEMPERATURE].v[0] * 10.0);
    }
    pkt->system_status = (uint8_t)a->fsm.state;
    pkt->sensor_status = sensor_status_word(a);
    bool baro_used = a->latest_valid[AERO_SLOT_BARO] && a->health[AERO_SLOT_BARO] == AERO_HEALTH_OK;
    bool acc_used = a->latest_valid[AERO_SLOT_ACCEL] && a->health[AERO_SLOT_ACCEL] == AERO_HEALTH_OK;
    pkt->nav_status = (uint8_t)((baro_used ? 1 : 0) | (acc_used ? 2 : 0) | (pkt->gnss_fix ? 4 : 0));
    return true;
}

size_t aero_app_sizeof(void) { return sizeof(aero_app_t); }
size_t aero_app_nv_sizeof(void) { return sizeof(aero_nv_state_t); }
aero_state_t aero_app_state(const aero_app_t *a) { return a->fsm.state; }
aero_reason_t aero_app_last_reason(const aero_app_t *a) { return a->fsm.last_reason; }
uint32_t aero_app_faults(const aero_app_t *a) { return a->faults; }
aero_health_t aero_app_health(const aero_app_t *a, int slot)
{
    return (slot >= 0 && slot < AERO_SLOT_COUNT) ? a->health[slot] : AERO_HEALTH_UNKNOWN;
}
float aero_app_max_est_alt(const aero_app_t *a) { return a->max_est_alt; }
uint32_t aero_app_boot_count(const aero_app_t *a) { return a->boot_count; }
const aero_nv_state_t *aero_app_nv(const aero_app_t *a) { return &a->nv; }
bool aero_app_nv_dirty(const aero_app_t *a) { return a->nv_dirty; }
