/**
 * @file crc.h
 * @brief The checksum of a MAVLink 2 frame with the CRC_EXTRA byte of its message, for C code.
 */
#ifndef MAV2SUM_CRC_H
#define MAV2SUM_CRC_H

#include "mav2sum/crc_extra.h"

#include <stddef.h>
#include <stdint.h>

#define MAV2SUM_MESSAGE_ID_AT 7U
#define MAV2SUM_MESSAGE_ID_END 10U

static inline uint16_t mav2sum_accumulate(uint16_t crc, uint8_t byte) {
    uint8_t tmp = (uint8_t)(byte ^ (crc & 0xFFU));
    tmp = (uint8_t)(tmp ^ (uint8_t)(tmp << 4U));
    return (uint16_t)((crc >> 8U) ^ ((uint16_t)tmp << 8U) ^ ((uint16_t)tmp << 3U) ^ (tmp >> 4U));
}

/// The CRC_EXTRA byte of the messages of the model (0 for any other message id).
static inline uint8_t mav2sum_extra(uint32_t message_id) {
#define MAV2SUM_CASE(id, extra)                                                                    \
    case id:                                                                                       \
        return extra;
    switch (message_id) {
        MAV2SUM_CRC_EXTRA(MAV2SUM_CASE)
    default:
        return 0U;
    }
#undef MAV2SUM_CASE
}

/**
 * CRC-16/MCRF4XX over the frame without its start byte, then the CRC_EXTRA byte of the message.
 * The message id is the three bytes from the eighth on, little endian.
 */
static inline uint64_t mav2sum_crc(const uint8_t* data, size_t length) {
    uint16_t crc = 0xFFFFU;
    for (size_t index = 1U; index < length; ++index) {
        crc = mav2sum_accumulate(crc, data[index]);
    }
    uint32_t message_id = 0U;
    if (length >= MAV2SUM_MESSAGE_ID_END) {
        message_id = (uint32_t)data[MAV2SUM_MESSAGE_ID_AT] |
                     ((uint32_t)data[MAV2SUM_MESSAGE_ID_AT + 1U] << 8U) |
                     ((uint32_t)data[MAV2SUM_MESSAGE_ID_AT + 2U] << 16U);
    }
    return mav2sum_accumulate(crc, mav2sum_extra(message_id));
}

#endif /* MAV2SUM_CRC_H */
