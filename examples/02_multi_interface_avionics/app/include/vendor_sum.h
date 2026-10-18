/**
 * @file vendor_sum.h
 * @brief Example custom checksum for generated C code: 16-bit sum of all bytes.
 */
#ifndef VENDOR_SUM_H
#define VENDOR_SUM_H

#include <stddef.h>
#include <stdint.h>

/// Sum of all bytes modulo 2^16 (matches plugins/vendor_sum.py and vendor::SumChecksum).
static inline uint64_t vendor_sum_c(const uint8_t* data, size_t length) {
    uint64_t sum = 0U;
    for (size_t index = 0U; index < length; ++index) {
        sum += data[index];
    }
    return sum & 0xFFFFU;
}

#endif /* VENDOR_SUM_H */
