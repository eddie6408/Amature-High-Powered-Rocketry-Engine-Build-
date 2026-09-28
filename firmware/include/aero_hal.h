/*
 * Hardware abstraction layer. Drivers depend only on these bus primitives, so
 * the same driver code runs on the target MCU, in HIL, and on the host (SIL).
 * A board support package (BSP) implements them for a specific MCU.
 */
#ifndef AERO_HAL_H
#define AERO_HAL_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum { AERO_OK = 0, AERO_ERR_TIMEOUT = -1, AERO_ERR_NACK = -2, AERO_ERR_BUS = -3,
               AERO_ERR_PARAM = -4 } aero_err_t;

typedef struct { uint8_t bus; uint8_t cs_pin; uint32_t hz; uint8_t mode; } aero_spi_dev_t;
typedef struct { uint8_t bus; uint8_t addr; } aero_i2c_dev_t;

aero_err_t aero_spi_transfer(const aero_spi_dev_t *d, const uint8_t *tx, uint8_t *rx, size_t n);
aero_err_t aero_i2c_write_read(const aero_i2c_dev_t *d, const uint8_t *tx, size_t ntx,
                               uint8_t *rx, size_t nrx);
aero_err_t aero_uart_write(uint8_t port, const uint8_t *data, size_t n);
size_t aero_uart_read(uint8_t port, uint8_t *data, size_t max);
aero_err_t aero_can_send(uint32_t id, const uint8_t *data, uint8_t len);

uint64_t aero_time_us(void);          /* monotonic microseconds since boot */
void aero_watchdog_kick(void);
float aero_battery_volts(void);
float aero_mcu_temperature_c(void);

/* Non-volatile state (backup SRAM / FRAM) used to survive processor resets. */
aero_err_t aero_nv_write(uint16_t key, const void *data, size_t n);
aero_err_t aero_nv_read(uint16_t key, void *data, size_t n);

#ifdef __cplusplus
}
#endif
#endif
