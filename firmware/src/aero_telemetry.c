#include "aero_telemetry.h"

#include <string.h>

uint16_t aero_crc16_ccitt(const uint8_t *data, size_t len, uint16_t crc)
{
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int b = 0; b < 8; b++) {
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

/* Explicit little-endian serialization: independent of host endianness and
 * struct padding. */
static uint8_t *put_u8(uint8_t *b, uint8_t v) { *b++ = v; return b; }
static uint8_t *put_u16(uint8_t *b, uint16_t v)
{
    b[0] = (uint8_t)v;
    b[1] = (uint8_t)(v >> 8);
    return b + 2;
}
static uint8_t *put_u32(uint8_t *b, uint32_t v)
{
    for (int i = 0; i < 4; i++) b[i] = (uint8_t)(v >> (8 * i));
    return b + 4;
}
static uint8_t *put_f32(uint8_t *b, float f)
{
    uint32_t v;
    memcpy(&v, &f, 4);
    return put_u32(b, v);
}
static const uint8_t *get_u8(const uint8_t *b, uint8_t *v) { *v = b[0]; return b + 1; }
static const uint8_t *get_u16(const uint8_t *b, uint16_t *v)
{
    *v = (uint16_t)(b[0] | (b[1] << 8));
    return b + 2;
}
static const uint8_t *get_u32(const uint8_t *b, uint32_t *v)
{
    *v = (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
    return b + 4;
}
static const uint8_t *get_f32(const uint8_t *b, float *f)
{
    uint32_t v;
    b = get_u32(b, &v);
    memcpy(f, &v, 4);
    return b;
}

size_t aero_tlm_encode(const aero_tlm_packet_t *p, uint8_t *buf)
{
    uint8_t *b = buf;
    b = put_u16(b, AERO_TLM_SYNC);
    b = put_u8(b, AERO_TLM_VERSION);
    b = put_u16(b, p->vehicle_id);
    b = put_u16(b, p->flight_id);
    b = put_u32(b, p->sequence);
    b = put_u32(b, p->timestamp_ms);
    b = put_f32(b, p->altitude);
    b = put_f32(b, p->velocity);
    b = put_f32(b, p->acceleration);
    b = put_u32(b, (uint32_t)p->lat_e7);
    b = put_u32(b, (uint32_t)p->lon_e7);
    for (int i = 0; i < 4; i++) b = put_u16(b, (uint16_t)p->attitude[i]);
    b = put_u16(b, p->battery_mv);
    b = put_u16(b, (uint16_t)p->temperature_c10);
    b = put_u8(b, p->system_status);
    b = put_u16(b, p->sensor_status);
    b = put_u8(b, p->nav_status);
    b = put_u8(b, p->gnss_fix);
    b = put_u8(b, p->gnss_sats);
    uint16_t crc = aero_crc16_ccitt(buf, (size_t)(b - buf), 0xFFFFu);
    b = put_u16(b, crc);
    return (size_t)(b - buf);
}

int aero_tlm_decode(const uint8_t *buf, size_t len, aero_tlm_packet_t *o)
{
    if (len != AERO_TLM_FRAME_SIZE) return -1;
    uint16_t crc_rx;
    get_u16(buf + AERO_TLM_FRAME_SIZE - 2, &crc_rx);
    if (aero_crc16_ccitt(buf, AERO_TLM_FRAME_SIZE - 2, 0xFFFFu) != crc_rx) return -2;
    const uint8_t *b = buf;
    uint16_t sync;
    uint8_t ver;
    b = get_u16(b, &sync);
    b = get_u8(b, &ver);
    if (sync != AERO_TLM_SYNC || ver != AERO_TLM_VERSION) return -3;
    uint32_t u;
    uint16_t h;
    b = get_u16(b, &o->vehicle_id);
    b = get_u16(b, &o->flight_id);
    b = get_u32(b, &o->sequence);
    b = get_u32(b, &o->timestamp_ms);
    b = get_f32(b, &o->altitude);
    b = get_f32(b, &o->velocity);
    b = get_f32(b, &o->acceleration);
    b = get_u32(b, &u); o->lat_e7 = (int32_t)u;
    b = get_u32(b, &u); o->lon_e7 = (int32_t)u;
    for (int i = 0; i < 4; i++) { b = get_u16(b, &h); o->attitude[i] = (int16_t)h; }
    b = get_u16(b, &o->battery_mv);
    b = get_u16(b, &h); o->temperature_c10 = (int16_t)h;
    b = get_u8(b, &o->system_status);
    b = get_u16(b, &o->sensor_status);
    b = get_u8(b, &o->nav_status);
    b = get_u8(b, &o->gnss_fix);
    get_u8(b, &o->gnss_sats);
    return 0;
}
