/**
 * @file crc.h
 * @brief The checksum of a MAVLink 1 frame with the CRC_EXTRA byte of its message, for C code.
 */
#ifndef MAVSUM_CRC_H
#define MAVSUM_CRC_H

#include <stddef.h>
#include <stdint.h>

#define MAVSUM_MESSAGE_ID_AT 5U

static inline uint16_t mavsum_accumulate(uint16_t crc, uint8_t byte) {
    uint8_t tmp = (uint8_t)(byte ^ (crc & 0xFFU));
    tmp = (uint8_t)(tmp ^ (uint8_t)(tmp << 4U));
    return (uint16_t)((crc >> 8U) ^ ((uint16_t)tmp << 8U) ^ ((uint16_t)tmp << 3U) ^ (tmp >> 4U));
}

/// The CRC_EXTRA byte of the messages of the model (0 for any other message id).
static inline uint8_t mavsum_extra(uint8_t message_id) {
    switch (message_id) {
    case 0U:
        return 50U;
    case 30U:
        return 39U;
    case 33U:
        return 104U;
    case 76U:
        return 152U;
    default:
        return 0U;
    }
}

/**
 * CRC-16/MCRF4XX over the frame without its start byte, then the CRC_EXTRA byte of the message.
 * The message id is the sixth byte of the frame.
 */
static inline uint64_t mavsum_crc(const uint8_t* data, size_t length) {
    uint16_t crc = 0xFFFFU;
    for (size_t index = 1U; index < length; ++index) {
        crc = mavsum_accumulate(crc, data[index]);
    }
    const uint8_t message_id = length > MAVSUM_MESSAGE_ID_AT ? data[MAVSUM_MESSAGE_ID_AT] : 0U;
    return mavsum_accumulate(crc, mavsum_extra(message_id));
}

#endif /* MAVSUM_CRC_H */
