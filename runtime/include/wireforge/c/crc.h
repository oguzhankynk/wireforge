/**
 * @file crc.h
 * @brief A parametric CRC engine (Rocksoft model), bitwise and without tables.
 */
#ifndef WIREFORGE_C_CRC_H
#define WIREFORGE_C_CRC_H

#include "wireforge/c/bits.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/**
 * The parameters of a CRC as listed in CRC catalogues.
 *
 * A generated checksum function holds them as constants and calls wf_crc().
 */
typedef struct {
    unsigned width;  ///< CRC width in bits, 1..64.
    uint64_t poly;   ///< Generator polynomial, normal (non-reflected) form.
    uint64_t init;   ///< Initial register value.
    bool refin;      ///< Reflect each input byte.
    bool refout;     ///< Reflect the final register.
    uint64_t xorout; ///< Value XOR-ed into the result.
} wf_crc_params;

/// Eight shift/xor steps of a left-aligned (bit 63 = top) CRC register.
static inline uint64_t wf_crc_step_byte(uint64_t reg, uint64_t aligned_poly) {
    for (unsigned bit = 0U; bit < 8U; ++bit) {
        reg = (reg & ((uint64_t)1U << 63U)) != 0U ? (reg << 1U) ^ aligned_poly : reg << 1U;
    }
    return reg;
}

/**
 * Computes the CRC of @p length bytes.
 * @param params The parameters; the width is 1..64 and the values fit it.
 * @return The CRC value, @p params->width bits wide.
 */
static inline uint64_t wf_crc(const wf_crc_params* params, const uint8_t* data, size_t length) {
    const unsigned shift = WF_MAX_BITS - params->width;
    const uint64_t aligned_poly = params->poly << shift;
    uint64_t reg = params->init << shift;
    for (size_t index = 0U; index < length; ++index) {
        const uint64_t input = params->refin ? wf_reflect(data[index], 8U) : data[index];
        reg = wf_crc_step_byte(reg ^ (input << 56U), aligned_poly);
    }
    const uint64_t value = reg >> shift;
    return (params->refout ? wf_reflect(value, params->width) : value) ^ params->xorout;
}

#endif /* WIREFORGE_C_CRC_H */
