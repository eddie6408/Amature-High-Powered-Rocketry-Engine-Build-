/* Host unit tests for the portable flight-software core. */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "aero_app.h"
#include "aero_fsm.h"
#include "aero_kalman.h"
#include "aero_sensor.h"
#include "aero_tasks.h"
#include "aero_telemetry.h"

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);      \
            failures++;                                                          \
        }                                                                        \
    } while (0)

static aero_fsm_inputs_t pad(double t)
{
    aero_fsm_inputs_t in;
    memset(&in, 0, sizeof in);
    in.t = t;
    in.accel_axial = 9.81f;
    in.accel_ok = true;
    in.baro_ok = true;
    return in;
}

static void arm(aero_fsm_t *f)
{
    aero_fsm_inputs_t in = pad(0.0);
    in.command = AERO_CMD_PREFLIGHT;
    aero_fsm_step(f, &in);
    in.t = 0.01;
    in.command = AERO_CMD_ARM;
    in.preflight_ok = false;
    aero_fsm_step(f, &in);
    CHECK(f->state == AERO_STATE_PREFLIGHT); /* arm refused without preflight */
    in.preflight_ok = true;
    aero_fsm_step(f, &in);
    CHECK(f->state == AERO_STATE_ARMED);
}

static void test_fsm_nominal(void)
{
    aero_fsm_t f;
    aero_fsm_init(&f, NULL);
    CHECK(f.state == AERO_STATE_SAFE);
    arm(&f);
    double t = 1.0;
    /* boost at 8 g for 1.5 s */
    float alt = 0, vel = 0;
    for (; t < 2.5; t += 0.01) {
        aero_fsm_inputs_t in = pad(t);
        in.accel_axial = 80.0f;
        vel += 70.0f * 0.01f;
        alt += vel * 0.01f;
        in.est_vel = vel;
        in.est_alt_agl = alt;
        in.baro_alt_agl = alt;
        aero_fsm_step(&f, &in);
    }
    CHECK(f.state == AERO_STATE_ASCENT);
    CHECK(f.last_reason == AERO_REASON_LIFTOFF_ACCEL || f.last_reason == AERO_REASON_LIFTOFF_BOTH);
    /* coast */
    for (; t < 30.0 && f.state != AERO_STATE_DESCENT; t += 0.01) {
        aero_fsm_inputs_t in = pad(t);
        in.accel_axial = -3.0f;
        vel -= 9.81f * 0.01f;
        alt += vel * 0.01f;
        in.est_vel = vel;
        in.est_alt_agl = alt;
        in.baro_alt_agl = alt;
        aero_fsm_step(&f, &in);
        if (vel > 0.0f) CHECK(f.state != AERO_STATE_DESCENT);
    }
    CHECK(f.state == AERO_STATE_DESCENT);
    CHECK(f.last_reason == AERO_REASON_APOGEE_VEL_BARO);
    CHECK(!isnan(f.t_apogee));
}

static void test_fsm_spike_rejected(void)
{
    aero_fsm_t f;
    aero_fsm_init(&f, NULL);
    arm(&f);
    /* a single 30 g sample and a single 500 m baro sample must not launch */
    aero_fsm_inputs_t in = pad(1.0);
    in.accel_axial = 300.0f;
    aero_fsm_step(&f, &in);
    in = pad(1.01);
    in.baro_alt_agl = 500.0f;
    aero_fsm_step(&f, &in);
    for (double t = 1.02; t < 3.0; t += 0.01) {
        in = pad(t);
        aero_fsm_step(&f, &in);
    }
    CHECK(f.state == AERO_STATE_ARMED);
}

static void test_fsm_no_return_to_ground(void)
{
    aero_fsm_t f;
    aero_fsm_init(&f, NULL);
    aero_fsm_restore(&f, AERO_STATE_COAST, 0.0, 500.0f, NAN);
    aero_fsm_inputs_t in = pad(5.0);
    in.command = AERO_CMD_DISARM;
    in.est_vel = 50.0f;
    aero_fsm_step(&f, &in);
    CHECK(f.state == AERO_STATE_COAST);
}

static void test_fsm_baro_degraded_apogee(void)
{
    aero_fsm_t f;
    aero_fsm_init(&f, NULL);
    aero_fsm_restore(&f, AERO_STATE_COAST, 0.0, 0.0f, NAN);
    double t = 5.0;
    for (; t < 5.5; t += 0.01) {
        aero_fsm_inputs_t in = pad(t);
        in.baro_ok = false;
        in.est_vel = -1.0f;
        aero_fsm_step(&f, &in);
    }
    CHECK(f.state == AERO_STATE_DESCENT);
    CHECK(f.last_reason == AERO_REASON_APOGEE_VEL_ONLY);
}

static void test_kalman(void)
{
    aero_kf_t kf;
    aero_kf_init(&kf, 2.0, 1.5, 0.0);
    /* constant 10 m/s^2 climb, perfect baro */
    double h = 0, v = 0;
    for (int i = 0; i < 300; i++) {
        double dt = 0.01;
        v += 10.0 * dt;
        h += v * dt;
        aero_kf_predict(&kf, 10.0, dt, -1.0);
        aero_kf_update(&kf, h, -1.0);
    }
    CHECK(fabs(kf.x[0] - h) < 0.5);
    CHECK(fabs(kf.x[1] - v) < 0.5);
}

static void test_telemetry_roundtrip(void)
{
    aero_tlm_packet_t p;
    memset(&p, 0, sizeof p);
    p.vehicle_id = 1;
    p.flight_id = 7;
    p.sequence = 123456;
    p.timestamp_ms = 99999;
    p.altitude = 1234.5f;
    p.velocity = -12.25f;
    p.acceleration = 9.5f;
    p.lat_e7 = 350000000;
    p.lon_e7 = -1170000000;
    p.attitude[0] = 32767;
    p.battery_mv = 8123;
    p.temperature_c10 = -45;
    p.system_status = AERO_STATE_COAST;
    p.sensor_status = 0x1234;
    p.nav_status = 3;
    p.gnss_fix = 3;
    p.gnss_sats = 11;
    uint8_t buf[AERO_TLM_FRAME_SIZE];
    CHECK(aero_tlm_encode(&p, buf) == AERO_TLM_FRAME_SIZE);
    aero_tlm_packet_t q;
    CHECK(aero_tlm_decode(buf, sizeof buf, &q) == 0);
    CHECK(q.sequence == p.sequence && q.lon_e7 == p.lon_e7 && q.altitude == p.altitude);
    CHECK(q.temperature_c10 == -45 && q.gnss_sats == 11);
    buf[20] ^= 0x01;
    CHECK(aero_tlm_decode(buf, sizeof buf, &q) == -2);
    /* CRC-16/CCITT-FALSE check value */
    CHECK(aero_crc16_ccitt((const uint8_t *)"123456789", 9, 0xFFFF) == 0x29B1);
}

static void test_validator(void)
{
    aero_limits_t lim = {-500.0f, 30000.0f, 1500.0f, 100, 1, 20, 10};
    aero_validator_t v;
    aero_validator_init(&v, &lim);
    aero_sample_t s = {1, 1, 0.0, 100.0f, AERO_SENSOR_STATUS_OK, 1.0f};
    CHECK(aero_validate(&v, &s, 0.0) == 0);
    CHECK(v.health == AERO_HEALTH_OK);
    s.timestamp = 0.02;
    s.value = 600.0f; /* 25 km/s: rate violation */
    CHECK(aero_validate(&v, &s, 0.02) & AERO_FAIL_RATE);
    CHECK(v.health == AERO_HEALTH_DEGRADED);
    s.timestamp = 0.01; /* time went backwards */
    s.value = 100.0f;
    CHECK(aero_validate(&v, &s, 0.03) & AERO_FAIL_TIMESTAMP);
    CHECK(aero_validate(&v, NULL, 0.05) & AERO_FAIL_MISSING);
    s.status = AERO_SENSOR_STATUS_COMM_ERROR;
    s.timestamp = 0.06;
    CHECK(aero_validate(&v, &s, 0.06) & AERO_FAIL_STATUS);
}

static void test_task_table(void)
{
    for (unsigned i = 0; i < AERO_TASK_COUNT; i++) {
        CHECK(AERO_TASKS[i].deadline_ms <= AERO_TASKS[i].period_ms * 3);
        CHECK(AERO_TASKS[i].period_ms > 0);
    }
}

static void pad_inputs(aero_input_t in[AERO_SLOT_COUNT], double t, double baro, double accel_x)
{
    /* real sensors always show LSB noise; a perfectly constant value is (correctly)
     * flagged as a stuck sensor, so dither the synthetic inputs */
    double d = 0.02 * sin(t * 977.0);
    accel_x += d;
    baro += 5.0 * d;
    memset(in, 0, sizeof(aero_input_t) * AERO_SLOT_COUNT);
    in[AERO_SLOT_ACCEL] = (aero_input_t){true, (uint32_t)(t * 100), t, {accel_x, 0.0, 0.0},
                                         AERO_SENSOR_STATUS_OK, 1.0f};
    in[AERO_SLOT_GYRO] = (aero_input_t){true, (uint32_t)(t * 100), t, {0.0, 0.0, 0.0},
                                        AERO_SENSOR_STATUS_OK, 1.0f};
    in[AERO_SLOT_BARO] = (aero_input_t){true, (uint32_t)(t * 100), t, {baro, 0.0, 0.0},
                                        AERO_SENSOR_STATUS_OK, 1.0f};
    in[AERO_SLOT_BATTERY] = (aero_input_t){true, (uint32_t)(t * 100), t, {8.1, 0.0, 0.0},
                                           AERO_SENSOR_STATUS_OK, 1.0f};
}

static void test_app_arm_launch_and_reset(void)
{
    aero_app_t app;
    aero_app_init(&app, NULL, NULL);
    CHECK(aero_app_state(&app) == AERO_STATE_SAFE);
    aero_input_t in[AERO_SLOT_COUNT];
    aero_tlm_packet_t pkt;
    double t = 0.0;
    int frames = 0;
    for (; t < 2.0; t += 0.01) {
        if (fabs(t - 0.5) < 0.005) aero_app_command(&app, AERO_CMD_PREFLIGHT, false);
        if (fabs(t - 1.0) < 0.005) aero_app_command(&app, AERO_CMD_ARM, true);
        pad_inputs(in, t, 100.0, 9.81);
        frames += aero_app_step(&app, t, in, &pkt);
    }
    CHECK(aero_app_state(&app) == AERO_STATE_ARMED);
    CHECK(frames >= 19 && frames <= 21);           /* 10 Hz telemetry */
    CHECK(pkt.system_status == AERO_STATE_ARMED);
    uint8_t buf[AERO_TLM_FRAME_SIZE];
    aero_tlm_packet_t back;
    aero_tlm_encode(&pkt, buf);
    CHECK(aero_tlm_decode(buf, sizeof buf, &back) == 0 && back.sequence == pkt.sequence);
    /* boost: 8 g, baro climbing */
    double alt = 0.0, vel = 0.0;
    for (; t < 3.0; t += 0.01) {
        vel += 70.0 * 0.01;
        alt += vel * 0.01;
        pad_inputs(in, t, 100.0 + alt, 80.0);
        aero_app_step(&app, t, in, &pkt);
    }
    CHECK(aero_app_state(&app) == AERO_STATE_ASCENT);
    /* processor reset: restart from the persisted NV state */
    aero_nv_state_t nv = *aero_app_nv(&app);
    aero_app_t app2;
    aero_app_init(&app2, NULL, &nv);
    CHECK(aero_app_state(&app2) == AERO_STATE_ASCENT);
    CHECK(aero_app_boot_count(&app2) == 2);
    /* a disarm command after the reset must not return to a ground state */
    aero_app_command(&app2, AERO_CMD_DISARM, false);
    pad_inputs(in, t, 100.0 + alt, 80.0);
    aero_app_step(&app2, t, in, &pkt);
    CHECK(aero_app_state(&app2) == AERO_STATE_ASCENT);
    CHECK(pkt.sequence > nv.tlm_sequence - 1);     /* telemetry sequence continues */
}

static void test_app_sensor_failure_flagged(void)
{
    aero_app_t app;
    aero_app_init(&app, NULL, NULL);
    aero_input_t in[AERO_SLOT_COUNT];
    for (double t = 0.0; t < 1.0; t += 0.01) {
        pad_inputs(in, t, 100.0, 9.81);
        if (t > 0.5) in[AERO_SLOT_BARO].status = AERO_SENSOR_STATUS_COMM_ERROR;
        aero_app_step(&app, t, in, NULL);
    }
    CHECK(aero_app_health(&app, AERO_SLOT_BARO) == AERO_HEALTH_FAILED);
    CHECK(aero_app_faults(&app) & (AERO_FAULT_SENSOR_BASE << AERO_SLOT_BARO));
    CHECK(aero_app_health(&app, AERO_SLOT_ACCEL) == AERO_HEALTH_OK);
}

static void test_app_stuck_sensor_detected(void)
{
    aero_app_t app;
    aero_app_init(&app, NULL, NULL);
    aero_input_t in[AERO_SLOT_COUNT];
    for (double t = 0.0; t < 1.0; t += 0.01) {
        pad_inputs(in, t, 100.0, 9.81);
        in[AERO_SLOT_ACCEL].v[0] = 9.81; /* frozen output */
        aero_app_step(&app, t, in, NULL);
    }
    CHECK(aero_app_health(&app, AERO_SLOT_ACCEL) != AERO_HEALTH_OK);
}

int main(void)
{
    test_app_arm_launch_and_reset();
    test_app_sensor_failure_flagged();
    test_app_stuck_sensor_detected();
    test_fsm_nominal();
    test_fsm_spike_rejected();
    test_fsm_no_return_to_ground();
    test_fsm_baro_degraded_apogee();
    test_kalman();
    test_telemetry_roundtrip();
    test_validator();
    test_task_table();
    if (failures) {
        fprintf(stderr, "%d failure(s)\n", failures);
        return 1;
    }
    printf("firmware unit tests: all passed\n");
    return 0;
}
