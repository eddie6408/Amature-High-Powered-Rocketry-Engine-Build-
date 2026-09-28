/* TELEMETRY-2 wire format. Byte-identical to
 * src/aerodyne/avionics/telemetry.py (see that module for the frame table). */
#ifndef AERO_TELEMETRY_H
#define AERO_TELEMETRY_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define AERO_TLM_SYNC 0xD1AEu
#define AERO_TLM_VERSION 2u
#define AERO_TLM_FRAME_SIZE 55u

typedef struct {
    uint16_t vehicle_id;
    uint16_t flight_id;
    uint32_t sequence;
    uint32_t timestamp_ms;
    float altitude;
    float velocity;
    float acceleration;
    int32_t lat_e7;
    int32_t lon_e7;
    int16_t attitude[4];
    uint16_t battery_mv;
    int16_t temperature_c10;
    uint8_t system_status;
    uint16_t sensor_status;
    uint8_t nav_status;
    uint8_t gnss_fix;
    uint8_t gnss_sats;
} aero_tlm_packet_t;

uint16_t aero_crc16_ccitt(const uint8_t *data, size_t len, uint16_t crc);
/* Serialize into buf (>= AERO_TLM_FRAME_SIZE bytes). Returns bytes written. */
size_t aero_tlm_encode(const aero_tlm_packet_t *p, uint8_t *buf);
/* Returns 0 on success, -1 bad length, -2 CRC, -3 sync/version. */
int aero_tlm_decode(const uint8_t *buf, size_t len, aero_tlm_packet_t *out);

#ifdef __cplusplus
}
#endif
#endif
