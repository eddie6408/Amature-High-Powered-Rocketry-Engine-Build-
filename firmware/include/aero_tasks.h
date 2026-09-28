/*
 * RTOS task table. Each task has a defined period, priority, deadline, stack
 * and failure behaviour; the health monitor enforces deadlines and the
 * hardware watchdog is only kicked when every critical task has checked in.
 * On FreeRTOS each entry becomes an xTaskCreateStatic() task that runs with
 * vTaskDelayUntil(). See docs/FIRMWARE.md.
 */
#ifndef AERO_TASKS_H
#define AERO_TASKS_H

#include <stdint.h>

typedef enum {
    AERO_ON_MISS_LOG = 0,          /* record an event, continue */
    AERO_ON_MISS_DEGRADE = 1,      /* mark the subsystem degraded, continue */
    AERO_ON_MISS_WATCHDOG = 2      /* withhold watchdog kick -> reset -> resume from NV state */
} aero_miss_policy_t;

typedef struct {
    const char *name;
    uint16_t period_ms;
    uint16_t deadline_ms;
    uint8_t priority;              /* higher = more urgent (FreeRTOS convention) */
    uint16_t stack_words;
    aero_miss_policy_t on_miss;
    uint8_t critical;              /* participates in the watchdog check-in */
} aero_task_def_t;

static const aero_task_def_t AERO_TASKS[] = {
    /* name              period deadline prio stack  on_miss                 crit */
    {"sensor",               10,      5,   6,  768, AERO_ON_MISS_WATCHDOG,   1},
    {"navigation",           10,      8,   5, 1024, AERO_ON_MISS_WATCHDOG,   1},
    {"state_estimation",     10,      9,   5,  512, AERO_ON_MISS_WATCHDOG,   1},
    {"flight_state",         10,     10,   5,  512, AERO_ON_MISS_WATCHDOG,   1},
    {"logging",              20,     50,   3, 1024, AERO_ON_MISS_DEGRADE,    0},
    {"telemetry",           100,    100,   2,  768, AERO_ON_MISS_LOG,        0},
    {"health_monitor",      100,    100,   4,  512, AERO_ON_MISS_WATCHDOG,   1},
    {"command",              50,    100,   2,  512, AERO_ON_MISS_LOG,        0},
    {"diagnostics",        1000,   1000,   1,  512, AERO_ON_MISS_LOG,        0},
};

#define AERO_TASK_COUNT (sizeof AERO_TASKS / sizeof AERO_TASKS[0])

#endif
