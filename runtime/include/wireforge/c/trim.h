/**
 * @file trim.h
 * @brief Payloads sent without their trailing zero bytes (MAVLink 2 style).
 *
 * An interface that trims payloads drops the zero bytes at the end of every encoded payload; the
 * receiver reads the missing bytes as zeros. wf_trimmed_size() gives the size to send and
 * wf_zero_extend() restores the full payload before it is decoded.
 */
#ifndef WIREFORGE_C_TRIM_H
#define WIREFORGE_C_TRIM_H

#include <stddef.h>
#include <stdint.h>

/// Size of a payload without its trailing zero bytes; the first byte of a non-empty payload stays.
static inline size_t wf_trimmed_size(const uint8_t* payload, size_t size) {
    while (size > 1U && payload[size - 1U] == 0U) {
        --size;
    }
    return size;
}

/**
 * Returns the payload to decode: a payload shorter than @p full bytes is copied into @p buffer
 * (at least @p full bytes) and padded with zeros; a longer one is returned unchanged, so the
 * decoder still sees bytes after the last field. @p extended_size receives the size.
 */
static inline const uint8_t* wf_zero_extend(const uint8_t* payload, size_t size, uint8_t* buffer,
                                            size_t full, size_t* extended_size) {
    if (size >= full) {
        *extended_size = size;
        return payload;
    }
    for (size_t index = 0U; index < full; ++index) {
        buffer[index] = index < size ? payload[index] : 0U;
    }
    *extended_size = full;
    return buffer;
}

#endif /* WIREFORGE_C_TRIM_H */
