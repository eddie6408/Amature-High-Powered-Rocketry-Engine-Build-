#include "aero_sensor.h"

#include <math.h>
#include <string.h>

void aero_validator_init(aero_validator_t *v, const aero_limits_t *lim)
{
    memset(v, 0, sizeof *v);
    v->lim = *lim;
    v->health = AERO_HEALTH_UNKNOWN;
}

uint32_t aero_validate(aero_validator_t *v, const aero_sample_t *s, double now)
{
    uint32_t f = 0;
    if (!s || !isfinite(s->value)) {
        f |= AERO_FAIL_MISSING;
    }
    if (s) {
        if (s->status != AERO_SENSOR_STATUS_OK) f |= AERO_FAIL_STATUS;
        if (isfinite(s->value)) {
            float x = s->value;
            if (x < v->lim.min_value || x > v->lim.max_value) f |= AERO_FAIL_RANGE;
            if (v->have_last) {
                if (s->timestamp <= v->last_t) {
                    f |= AERO_FAIL_TIMESTAMP;
                } else if (fabsf(x - v->last_v) / (float)(s->timestamp - v->last_t) > v->lim.max_rate) {
                    f |= AERO_FAIL_RATE;
                }
            }
            if (s->timestamp > now + 0.05) f |= AERO_FAIL_TIMESTAMP;
            if (v->lim.stuck_count && v->have_last && x == v->last_v) {
                if (++v->same >= v->lim.stuck_count) f |= AERO_FAIL_STALE;
            } else {
                v->same = 0;
            }
            if (!(f & AERO_FAIL_TIMESTAMP)) v->last_t = s->timestamp;
            if (!(f & (AERO_FAIL_RANGE | AERO_FAIL_RATE))) {
                v->last_v = x;
                v->have_last = true;
            } else if (!v->have_last) {
                v->last_t = s->timestamp;
            }
        }
    }
    if (f) {
        v->good = 0;
        if (v->bad < UINT16_MAX) v->bad++;
        if (v->bad >= v->lim.fail_after) {
            v->health = AERO_HEALTH_FAILED;
        } else if (v->bad >= v->lim.degrade_after && v->health != AERO_HEALTH_FAILED) {
            v->health = AERO_HEALTH_DEGRADED;
        }
    } else {
        v->bad = 0;
        if (v->good < UINT16_MAX) v->good++;
        if (v->health == AERO_HEALTH_UNKNOWN || v->good >= v->lim.recover_after) {
            v->health = AERO_HEALTH_OK;
        }
    }
    return f;
}
