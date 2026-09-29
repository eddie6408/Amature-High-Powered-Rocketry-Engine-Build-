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

#define AERO_TLM_ID_SYNC 0xD2AEu
#define AERO_TLM_ID_FRAME_SIZE 117u

/* Identity frame: who the flight computer is (sections 38/39 of the spec).
 * Strings are zero-padded, not necessarily NUL-terminated when full. */
typedef struct {
    uint16_t vehicle_id;
    uint16_t flight_id;
    uint32_t sequence;
    char fw_version[16];
    char commit[12];
    uint8_t firmware_hash[32];   /* SHA-256 of the image; zeros = not provisioned */
    uint8_t config_hash[32];     /* SHA-256 of the flight configuration */
    char hw_version[12];
} aero_tlm_identity_t;

size_t aero_tlm_encode_identity(const aero_tlm_identity_t *id, uint8_t *buf);
int aero_tlm_decode_identity(const uint8_t *buf, size_t len, aero_tlm_identity_t *out);

uint16_t aero_crc16_ccitt(const uint8_t *data, size_t len, uint16_t crc);
/* Serialize into buf (>= AERO_TLM_FRAME_SIZE bytes). Returns bytes written. */
size_t aero_tlm_encode(const aero_tlm_packet_t *p, uint8_t *buf);
/* Returns 0 on success, -1 bad length, -2 CRC, -3 sync/version. */
int aero_tlm_decode(const uint8_t *buf, size_t len, aero_tlm_packet_t *out);

#ifdef __cplusplus
}
#endif
#endif
