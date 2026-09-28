/* Generic sensor sample + validation (range, rate, timestamp, comm status,
 * missing/NaN, stale/stuck). Mirrors src/aerodyne/avionics/sensors.py. */
#ifndef AERO_SENSOR_H
#define AERO_SENSOR_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum { AERO_HEALTH_OK = 0, AERO_HEALTH_DEGRADED = 1, AERO_HEALTH_FAILED = 2,
               AERO_HEALTH_UNKNOWN = 3 } aero_health_t;

typedef enum { AERO_SENSOR_STATUS_OK = 0, AERO_SENSOR_STATUS_COMM_ERROR = 1,
               AERO_SENSOR_STATUS_NOT_READY = 2, AERO_SENSOR_STATUS_NO_FIX = 3 } aero_sensor_status_t;

typedef enum {
    AERO_FAIL_MISSING = 1u << 0,
    AERO_FAIL_STATUS = 1u << 1,
    AERO_FAIL_RANGE = 1u << 2,
    AERO_FAIL_RATE = 1u << 3,
    AERO_FAIL_TIMESTAMP = 1u << 4,
    AERO_FAIL_STALE = 1u << 5
} aero_fail_bits_t;

typedef struct {
    uint16_t sensor_id;
    uint32_t sequence;
    double timestamp;        /* s, monotonic */
    float value;
    aero_sensor_status_t status;
    float quality;           /* 0..1 */
} aero_sample_t;

typedef struct {
    float min_value, max_value;
    float max_rate;          /* units/s */
    uint16_t stuck_count;    /* 0 disables */
    uint16_t degrade_after, fail_after, recover_after;
} aero_limits_t;

typedef struct {
    aero_limits_t lim;
    aero_health_t health;
    bool have_last;
    double last_t;
    float last_v;
    uint16_t same, bad, good;
} aero_validator_t;

void aero_validator_init(aero_validator_t *v, const aero_limits_t *lim);
/* sample may be NULL (missing). Returns a bitmask of aero_fail_bits_t (0 = valid). */
uint32_t aero_validate(aero_validator_t *v, const aero_sample_t *s, double now);

#ifdef __cplusplus
}
#endif
#endif
